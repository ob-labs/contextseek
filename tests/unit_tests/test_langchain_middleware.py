"""Unit tests for ``ContextSeekMiddleware``.

These tests do **not** spin up OceanBase. They mock ContextSeek and
validate the middleware contract: hook semantics, retrieval injection,
storage gating, throttled compact, and helper correctness.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("langchain", reason="langchain extra not installed")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402

from contextseek.bridges.langchain.middleware import ContextSeekMiddleware
from contextseek.domain.provenance import SourceType


# ---------------------------------------------------------------------------
# _tracing shim — @traceable graceful degradation
# ---------------------------------------------------------------------------


class TestTraceableShim:
    """The ``traceable`` decorator must preserve function behavior whether or
    not langsmith is installed, in both bare and parametrized forms."""

    def test_bare_decorator_preserves_behavior(self) -> None:
        from contextseek.bridges.langchain._tracing import traceable

        @traceable
        def add(a, b):
            return a + b

        assert add(2, 3) == 5

    def test_parametrized_decorator_preserves_behavior(self) -> None:
        from contextseek.bridges.langchain._tracing import traceable

        @traceable(run_type="retriever", name="X")
        def mul(a, b):
            return a * b

        assert mul(2, 3) == 6

    def test_noop_fallback_when_langsmith_absent(self, monkeypatch) -> None:
        """Force the langsmith-absent branch: ``traceable`` returns the
        undecorated function unchanged in both calling forms."""
        import contextseek.bridges.langchain._tracing as tracing

        monkeypatch.setattr(tracing, "_HAS_LANGSMITH", False)

        def fn(x):
            return x + 1

        # Bare form returns the same function object (identity passthrough).
        assert tracing.traceable(fn) is fn
        # Parametrized form returns a decorator that is an identity passthrough.
        decorated = tracing.traceable(run_type="tool", name="Y")(fn)
        assert decorated is fn
        assert decorated(41) == 42


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_ctx() -> MagicMock:
    """A ContextSeek mock with the API surface the middleware uses."""
    ctx = MagicMock(name="ContextSeek")
    ctx.retrieve.return_value = SimpleNamespace(items=[])
    ctx.add.return_value = None
    ctx.compact.return_value = None
    return ctx


def _hit(
    summary: str = "", abstract: str = "", item_id: str | None = None
) -> SimpleNamespace:
    import uuid

    return SimpleNamespace(
        item=SimpleNamespace(
            summary=summary,
            abstract=abstract,
            id=item_id or uuid.uuid4().hex,
        )
    )


def _retrieve_response(*hits: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(items=list(hits))


def _make_request(
    messages: list,
    *,
    system_message: SystemMessage | None = None,
) -> MagicMock:
    """Mimic the ModelRequest contract just enough for wrap_model_call."""
    req = MagicMock(name="ModelRequest")
    req.messages = messages
    req.system_message = system_message

    def _override(**kwargs):
        new_req = MagicMock(name="OverriddenRequest")
        new_req.messages = kwargs.get("messages", req.messages)
        new_req.system_message = kwargs.get("system_message", req.system_message)
        return new_req

    req.override.side_effect = _override
    return req


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_uses_injected_ctx(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(
            ctx=ctx,
            retrieval_k=7,
            auto_store=False,
            auto_compact=True,
            compact_every=5,
            scope="proj-x",
        )
        assert mw.ctx is ctx
        assert mw.retrieval_k == 7
        assert mw.auto_store is False
        assert mw.auto_compact is True
        assert mw.compact_every == 5
        assert mw._scope == "proj-x"
        assert mw._compact_counters == {}
        assert mw._compact_locks == {}


# ---------------------------------------------------------------------------
# before_agent
# ---------------------------------------------------------------------------


class TestBeforeAgent:
    def test_resolves_scope_from_thread_id(self) -> None:
        mw = ContextSeekMiddleware(ctx=_fake_ctx())
        runtime = SimpleNamespace(thread_id="t-1")
        assert mw.before_agent(state={"messages": []}, runtime=runtime) is None
        assert mw._current_scope() == "t-1"

    def test_keeps_explicit_scope(self) -> None:
        mw = ContextSeekMiddleware(ctx=_fake_ctx(), scope="explicit")
        runtime = SimpleNamespace(thread_id="t-1")
        mw.before_agent(state={"messages": []}, runtime=runtime)
        assert mw._current_scope() == "explicit"
        # ``self._scope`` is the constructor default, never mutated by before_agent
        assert mw._scope == "explicit"

    def test_falls_back_to_default(self) -> None:
        mw = ContextSeekMiddleware(ctx=_fake_ctx())
        runtime = SimpleNamespace()  # no thread_id attr
        mw.before_agent(state={"messages": []}, runtime=runtime)
        assert mw._current_scope() == "default"

    def test_concurrent_scopes_are_isolated(self) -> None:
        """A single middleware instance shared across two concurrent contexts
        must keep each context's scope separate (the core fix for issue 1)."""
        import contextvars

        mw = ContextSeekMiddleware(ctx=_fake_ctx())
        results: dict[str, str] = {}
        ready = threading.Barrier(2)

        def worker(thread_id: str, key: str) -> None:
            mw.before_agent(
                state={"messages": []},
                runtime=SimpleNamespace(thread_id=thread_id),
            )
            ready.wait(timeout=2.0)
            results[key] = mw._current_scope()

        # Each thread gets its own copy of the parent context — ContextVar.set
        # stays local to that copy.
        ctx_a = contextvars.copy_context()
        ctx_b = contextvars.copy_context()
        t_a = threading.Thread(target=ctx_a.run, args=(worker, "thr-A", "a"))
        t_b = threading.Thread(target=ctx_b.run, args=(worker, "thr-B", "b"))
        t_a.start()
        t_b.start()
        t_a.join(timeout=3.0)
        t_b.join(timeout=3.0)

        assert results == {"a": "thr-A", "b": "thr-B"}


# ---------------------------------------------------------------------------
# wrap_model_call
# ---------------------------------------------------------------------------


class TestWrapModelCall:
    def test_no_user_message_skips_retrieval(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, scope="s")
        request = _make_request([])
        handler = MagicMock(return_value="response")

        result = mw.wrap_model_call(request, handler)

        assert result == "response"
        handler.assert_called_once_with(request)
        ctx.retrieve.assert_not_called()

    def test_empty_retrieval_skips_override(self) -> None:
        ctx = _fake_ctx()
        ctx.retrieve.return_value = _retrieve_response()
        mw = ContextSeekMiddleware(ctx=ctx, scope="s")
        request = _make_request([HumanMessage(content="question?")])
        handler = MagicMock(return_value="response")

        result = mw.wrap_model_call(request, handler)

        assert result == "response"
        ctx.retrieve.assert_called_once()
        handler.assert_called_once_with(request)
        request.override.assert_not_called()

    def test_injects_context_block(self) -> None:
        ctx = _fake_ctx()
        ctx.retrieve.return_value = _retrieve_response(
            _hit(summary="OB is fast"), _hit(abstract="vector + fts")
        )
        mw = ContextSeekMiddleware(ctx=ctx, scope="s", retrieval_k=3)
        request = _make_request(
            [HumanMessage(content="What is OB?")],
            system_message=SystemMessage(content="You are helpful."),
        )
        captured = {}

        def handler(req):
            captured["sys"] = req.system_message
            return "response"

        result = mw.wrap_model_call(request, handler)

        assert result == "response"
        ctx.retrieve.assert_called_once_with(
            "What is OB?", scope="s", k=3, tags=None, min_score=None
        )
        assert isinstance(captured["sys"], SystemMessage)
        assert (
            "[Relevant Context - Prior Session Policy Lessons]"
            in captured["sys"].content
        )
        assert "OB is fast" in captured["sys"].content
        assert "vector + fts" in captured["sys"].content
        assert "You are helpful." in captured["sys"].content

    def test_retrieval_failure_falls_back_to_handler(self) -> None:
        ctx = _fake_ctx()
        ctx.retrieve.side_effect = RuntimeError("OB down")
        mw = ContextSeekMiddleware(ctx=ctx, scope="s")
        request = _make_request([HumanMessage(content="hi")])
        handler = MagicMock(return_value="response")

        result = mw.wrap_model_call(request, handler)

        assert result == "response"
        handler.assert_called_once_with(request)


# ---------------------------------------------------------------------------
# after_model
# ---------------------------------------------------------------------------


class TestAfterModel:
    def test_auto_store_off_skips(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, auto_store=False, scope="s")
        mw.after_model(state={"messages": [AIMessage(content="hello")]}, runtime=None)
        ctx.add.assert_not_called()

    def test_no_ai_message_skips(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, scope="s")
        mw.after_model(state={"messages": [HumanMessage(content="hi")]}, runtime=None)
        ctx.add.assert_not_called()

    def test_stores_with_correct_metadata(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, scope="s")
        mw.after_model(
            state={
                "messages": [HumanMessage(content="hi"), AIMessage(content="reply")]
            },
            runtime=None,
        )
        ctx.add.assert_called_once()
        kwargs = ctx.add.call_args.kwargs
        assert kwargs["content"] == "Q: hi\nA: reply"
        assert kwargs["scope"] == "s"
        assert kwargs["source"] == "agent_response"
        assert kwargs["source_type"] == SourceType.agent_inference
        assert "langchain" in kwargs["tags"]
        assert "agent_response" in kwargs["tags"]

    def test_storage_failure_is_silent(self) -> None:
        ctx = _fake_ctx()
        ctx.add.side_effect = RuntimeError("disk full")
        mw = ContextSeekMiddleware(ctx=ctx, scope="s")
        # Should not raise
        result = mw.after_model(
            state={"messages": [AIMessage(content="reply")]}, runtime=None
        )
        assert result is None

    def test_empty_ai_content_skips(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, scope="s")
        mw.after_model(state={"messages": [AIMessage(content="")]}, runtime=None)
        ctx.add.assert_not_called()

    def test_skip_when_no_human_message(self) -> None:
        """Issue 3: AI message without preceding HumanMessage must not be stored."""
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, scope="s")
        mw.after_model(
            state={"messages": [AIMessage(content="hello, I'm an agent")]},
            runtime=None,
        )
        ctx.add.assert_not_called()


# ---------------------------------------------------------------------------
# wrap_tool_call
# ---------------------------------------------------------------------------


class TestWrapToolCall:
    def _request(
        self, *, tool_name: str = "search", tool_id: str = "tc-1", messages=None
    ) -> SimpleNamespace:
        if messages is None:
            messages = [
                HumanMessage(content="find OB info"),
                AIMessage(
                    content="Let me search.",
                    tool_calls=[
                        {"name": tool_name, "args": {"q": "OB"}, "id": tool_id}
                    ],
                ),
            ]
        return SimpleNamespace(
            tool=SimpleNamespace(name=tool_name),
            tool_call={"name": tool_name, "args": {"q": "OB"}, "id": tool_id},
            state={"messages": messages},
        )

    def test_records_tool_message_as_string(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, scope="s", record_tool_calls=True)
        request = self._request()
        tool_msg = ToolMessage(content="OB is a database", tool_call_id="tc-1")
        handler = MagicMock(return_value=tool_msg)

        result = mw.wrap_tool_call(request, handler)

        assert result is tool_msg
        handler.assert_called_once_with(request)
        ctx.add.assert_called_once()
        kwargs = ctx.add.call_args.kwargs
        content = kwargs["content"]
        assert content["tool"] == "search"
        assert content["args"] == {"q": "OB"}
        assert content["result"] == "OB is a database"  # stringified
        assert content["rationale"] == "Let me search."  # AIMessage content
        assert content["task"] == "find OB info"  # last HumanMessage
        assert kwargs["source"] == "tool_execution"
        assert kwargs["source_type"] == SourceType.trace_extraction
        assert kwargs["tags"] == ["langchain", "tool", "search"]

    def test_handler_returning_plain_string(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, scope="s", record_tool_calls=True)
        request = self._request()
        handler = MagicMock(return_value="plain string result")

        result = mw.wrap_tool_call(request, handler)

        assert result == "plain string result"
        ctx.add.assert_called_once()
        assert ctx.add.call_args.kwargs["content"]["result"] == "plain string result"

    def test_rationale_none_when_no_matching_tool_call(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, scope="s", record_tool_calls=True)
        # AIMessage has no tool_calls matching id
        messages = [
            HumanMessage(content="task"),
            AIMessage(content="thinking", tool_calls=[]),
        ]
        request = self._request(messages=messages)
        handler = MagicMock(return_value=ToolMessage(content="r", tool_call_id="tc-1"))

        mw.wrap_tool_call(request, handler)

        assert ctx.add.call_args.kwargs["content"]["rationale"] is None

    def test_storage_failure_does_not_break_tool(self) -> None:
        ctx = _fake_ctx()
        ctx.add.side_effect = RuntimeError("oops")
        mw = ContextSeekMiddleware(ctx=ctx, scope="s", record_tool_calls=True)
        request = self._request()
        handler = MagicMock(return_value="ok")

        # Must not raise; tool result must still be returned
        result = mw.wrap_tool_call(request, handler)

        assert result == "ok"

    def test_default_does_not_record_tool_call(self) -> None:
        # Default record_tool_calls=False: tool runs, result returned, no add().
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, scope="s")
        request = self._request()
        tool_msg = ToolMessage(content="OB is a database", tool_call_id="tc-1")
        handler = MagicMock(return_value=tool_msg)

        result = mw.wrap_tool_call(request, handler)

        assert result is tool_msg
        handler.assert_called_once_with(request)
        ctx.add.assert_not_called()

    def test_arg_overrides_apply_even_when_not_recording(self) -> None:
        # Arg overrides affect tool *execution* and must still apply with the
        # default record_tool_calls=False.
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(
            ctx=ctx,
            scope="s",
            tool_arg_overrides={"search": {"q": "FORCED"}},
        )
        request = self._request()
        handler = MagicMock(return_value="ok")

        result = mw.wrap_tool_call(request, handler)

        assert result == "ok"
        ctx.add.assert_not_called()
        # Override merged into the request's tool_call args before execution.
        assert request.tool_call["args"]["q"] == "FORCED"


# ---------------------------------------------------------------------------
# after_agent (compact throttling)
# ---------------------------------------------------------------------------


class TestAfterAgent:
    def test_auto_compact_off_does_not_increment(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, auto_compact=False, scope="s")
        for _ in range(5):
            mw.after_agent(state={"messages": []}, runtime=None)
        assert mw._compact_counters == {}
        ctx.compact.assert_not_called()

    def test_throttle_fires_on_threshold(self) -> None:
        ctx = _fake_ctx()
        # Use an Event so we can wait for the daemon thread to run
        compact_done = threading.Event()
        ctx.compact.side_effect = lambda **kw: compact_done.set()

        mw = ContextSeekMiddleware(
            ctx=ctx, auto_compact=True, compact_every=3, scope="s"
        )

        mw.after_agent(state={"messages": []}, runtime=None)
        mw.after_agent(state={"messages": []}, runtime=None)
        # Not yet
        assert ctx.compact.call_count == 0
        assert mw._compact_counters["s"] == 2

        mw.after_agent(state={"messages": []}, runtime=None)
        # Counter resets
        assert mw._compact_counters["s"] == 0

        # Wait for the daemon thread
        assert compact_done.wait(timeout=2.0)
        assert ctx.compact.call_count == 1
        ctx.compact.assert_called_with(scope="s", dry_run=False)

    def test_concurrent_compact_skipped_when_locked(self) -> None:
        ctx = _fake_ctx()

        # Make compact slow so the lock is held when we trigger again
        release = threading.Event()
        started = threading.Event()

        def slow_compact(**kw):
            started.set()
            release.wait(timeout=2.0)

        ctx.compact.side_effect = slow_compact

        mw = ContextSeekMiddleware(
            ctx=ctx, auto_compact=True, compact_every=1, scope="s"
        )

        # First call: starts thread, holds lock
        mw.after_agent(state={"messages": []}, runtime=None)
        assert started.wait(timeout=2.0)

        # Second call: lock held, should skip
        mw.after_agent(state={"messages": []}, runtime=None)

        # Let the first compact finish
        release.set()
        # Wait a moment for the thread to release the lock
        time.sleep(0.1)

        # Only one compact invocation
        assert ctx.compact.call_count == 1
        mw.shutdown(wait=True)

    def test_shutdown_waits_for_inflight_compact(self) -> None:
        """Issue 5: shutdown(wait=True) must block until in-flight compact done."""
        ctx = _fake_ctx()
        compact_running = threading.Event()
        compact_done = threading.Event()

        def slow_compact(**_kw):
            compact_running.set()
            time.sleep(0.1)
            compact_done.set()

        ctx.compact.side_effect = slow_compact

        mw = ContextSeekMiddleware(
            ctx=ctx, auto_compact=True, compact_every=1, scope="s"
        )
        mw.after_agent(state={"messages": []}, runtime=None)
        # Wait for the worker thread to start the slow compact
        assert compact_running.wait(timeout=2.0)
        # Shutdown blocks until the in-flight task finishes
        mw.shutdown(wait=True)
        assert compact_done.is_set()
        assert ctx.compact.call_count == 1

    def test_after_shutdown_no_new_submission(self) -> None:
        """Submitting after shutdown drops the task and releases the lock."""
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(
            ctx=ctx, auto_compact=True, compact_every=1, scope="s"
        )
        mw.shutdown(wait=True)
        # Should not raise — the RuntimeError from a dead executor is swallowed.
        mw.after_agent(state={"messages": []}, runtime=None)
        # And no compact call happened.
        ctx.compact.assert_not_called()


# ---------------------------------------------------------------------------
# after_agent (dream dual-gate: count + min interval)
# ---------------------------------------------------------------------------


class TestAfterAgentDream:
    def test_auto_dream_off_by_default(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, scope="s")
        assert mw.auto_dream is False
        for _ in range(5):
            mw.after_agent(state={"messages": []}, runtime=None)
        assert mw._dream_counters == {}
        ctx.dream.assert_not_called()

    def test_fires_on_count_threshold_when_interval_clear(self) -> None:
        ctx = _fake_ctx()
        dream_done = threading.Event()
        ctx.dream.side_effect = lambda **kw: dream_done.set()

        mw = ContextSeekMiddleware(
            ctx=ctx,
            scope="s",
            auto_dream=True,
            dream_every=3,
            dream_min_interval_seconds=0.0,  # time gate always clear
        )
        mw.after_agent(state={"messages": []}, runtime=None)
        mw.after_agent(state={"messages": []}, runtime=None)
        assert ctx.dream.call_count == 0
        assert mw._dream_counters["s"] == 2

        mw.after_agent(state={"messages": []}, runtime=None)
        assert mw._dream_counters["s"] == 0  # counter reset on fire

        assert dream_done.wait(timeout=2.0)
        assert ctx.dream.call_count == 1
        ctx.dream.assert_called_with(scope="s", dry_run=False)

    def test_interval_gate_blocks_when_too_soon(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(
            ctx=ctx,
            scope="s",
            auto_dream=True,
            dream_every=1,
            dream_min_interval_seconds=3600.0,
        )
        # Pretend we dreamed just now → time gate must block.
        from datetime import datetime, timezone

        mw._last_dream["s"] = datetime.now(timezone.utc)
        mw.after_agent(state={"messages": []}, runtime=None)
        ctx.dream.assert_not_called()
        # Counter parked at threshold (not reset) so next run re-checks.
        assert mw._dream_counters["s"] == 1

    def test_dream_independent_of_compact(self) -> None:
        """auto_compact off but auto_dream on → dream still fires."""
        ctx = _fake_ctx()
        dream_done = threading.Event()
        ctx.dream.side_effect = lambda **kw: dream_done.set()

        mw = ContextSeekMiddleware(
            ctx=ctx,
            scope="s",
            auto_compact=False,
            auto_dream=True,
            dream_every=1,
            dream_min_interval_seconds=0.0,
        )
        mw.after_agent(state={"messages": []}, runtime=None)
        assert dream_done.wait(timeout=2.0)
        assert ctx.dream.call_count == 1
        ctx.compact.assert_not_called()
        mw.shutdown(wait=True)


# ---------------------------------------------------------------------------
# _resolve_vector_dims
# ---------------------------------------------------------------------------


class TestResolveVectorDims:
    def test_settings_dims_wins(self) -> None:
        settings = SimpleNamespace(dims=1024)
        embedder = MagicMock()
        embedder.embed_query.return_value = [0.0] * 384
        assert ContextSeekMiddleware._resolve_vector_dims(embedder, settings) == 1024
        embedder.embed_query.assert_not_called()

    def test_probe_when_settings_zero(self) -> None:
        settings = SimpleNamespace(dims=0)
        embedder = MagicMock()
        embedder.embed_query.return_value = [0.0] * 384
        assert ContextSeekMiddleware._resolve_vector_dims(embedder, settings) == 384

    def test_fallback_when_no_embedder(self) -> None:
        settings = SimpleNamespace(dims=0)
        assert ContextSeekMiddleware._resolve_vector_dims(None, settings) == 1536

    def test_fallback_when_probe_fails(self) -> None:
        settings = SimpleNamespace(dims=0)
        embedder = MagicMock()
        embedder.embed_query.side_effect = RuntimeError("network down")
        assert ContextSeekMiddleware._resolve_vector_dims(embedder, settings) == 1536


# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_last_user_text_finds_most_recent(self) -> None:
        msgs = [
            HumanMessage(content="first"),
            AIMessage(content="reply"),
            HumanMessage(content="second"),
        ]
        assert ContextSeekMiddleware._last_user_text(msgs) == "second"

    def test_last_user_text_none_when_empty(self) -> None:
        assert ContextSeekMiddleware._last_user_text([]) is None

    def test_last_ai_message(self) -> None:
        ai = AIMessage(content="hi")
        msgs = [HumanMessage(content="q"), ai, HumanMessage(content="next")]
        assert ContextSeekMiddleware._last_ai_message(msgs) is ai

    def test_reasoning_truncates_long_content(self) -> None:
        long = "x" * 5000
        msgs = [
            AIMessage(
                content=long,
                tool_calls=[{"name": "t", "args": {}, "id": "abc"}],
            ),
        ]
        result = ContextSeekMiddleware._reasoning_for_tool_call(
            msgs, "abc", max_chars=100
        )
        assert result is not None
        assert len(result) == 100

    def test_reasoning_returns_none_for_unknown_id(self) -> None:
        msgs = [
            AIMessage(
                content="thinking",
                tool_calls=[{"name": "t", "args": {}, "id": "abc"}],
            ),
        ]
        assert (
            ContextSeekMiddleware._reasoning_for_tool_call(msgs, "different-id") is None
        )

    def test_append_to_system_creates_when_none(self) -> None:
        result = ContextSeekMiddleware._append_to_system(None, "[ctx]")
        assert isinstance(result, SystemMessage)
        assert result.content == "[ctx]"

    def test_append_to_system_str_concat(self) -> None:
        existing = SystemMessage(content="base")
        result = ContextSeekMiddleware._append_to_system(existing, "[ctx]")
        assert result.content == "base\n[ctx]"

    def test_append_to_system_content_blocks(self) -> None:
        existing = SystemMessage(content=[{"type": "text", "text": "hi"}])
        result = ContextSeekMiddleware._append_to_system(existing, "[ctx]")
        assert isinstance(result.content, list)
        assert len(result.content) == 2
        assert result.content[-1] == {"type": "text", "text": "[ctx]"}

    def test_stringify_tool_result_for_message(self) -> None:
        msg = ToolMessage(content="hello", tool_call_id="x")
        assert ContextSeekMiddleware._stringify_tool_result(msg) == "hello"

    def test_stringify_tool_result_for_plain(self) -> None:
        assert ContextSeekMiddleware._stringify_tool_result("raw") == "raw"
        assert ContextSeekMiddleware._stringify_tool_result(None) == ""

    def test_stringify_tool_result_for_list_content(self) -> None:
        msg = ToolMessage(content=[{"type": "text", "text": "a"}], tool_call_id="x")
        result = ContextSeekMiddleware._stringify_tool_result(msg)
        assert isinstance(result, str)
        assert "a" in result


# ---------------------------------------------------------------------------
# Async wrappers (issue 2: asyncio.to_thread offload)
# ---------------------------------------------------------------------------


import asyncio  # noqa: E402  — kept local to async test block


def _asyncio_to_thread_available() -> bool:
    code = (
        "import asyncio\n"
        "async def main():\n"
        "    await asyncio.wait_for(asyncio.to_thread(lambda: 1), timeout=1)\n"
        "asyncio.run(main())\n"
    )
    try:
        return (
            subprocess.run(
                [sys.executable, "-c", code],
                timeout=3,
                check=False,
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )
    except subprocess.TimeoutExpired:
        return False


@pytest.mark.skipif(
    not _asyncio_to_thread_available(),
    reason="asyncio.to_thread does not complete in this Python environment",
)
class TestAsyncWrappers:
    def test_aafter_model_offloads_to_thread(self) -> None:
        """``aafter_model`` runs ``ctx.add`` on a worker thread, not the loop."""
        ctx = _fake_ctx()
        seen_threads: list[int] = []

        def slow_add(**_kw):
            seen_threads.append(threading.get_ident())
            time.sleep(0.02)

        ctx.add.side_effect = slow_add

        mw = ContextSeekMiddleware(ctx=ctx, scope="s")
        state = {"messages": [HumanMessage(content="q"), AIMessage(content="a")]}

        async def runner():
            main_thread = threading.get_ident()
            await mw.aafter_model(state=state, runtime=None)
            return main_thread

        main_id = asyncio.run(runner())

        assert ctx.add.call_count == 1
        assert seen_threads, "ctx.add was not called on a worker thread"
        # The work happened OFF the asyncio thread.
        assert seen_threads[0] != main_id

    def test_aafter_model_propagates_scope_to_thread(self) -> None:
        """``ContextVar`` set by ``before_agent`` must reach the worker thread."""
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx)
        # Constructor scope=None; before_agent sets the per-task scope.

        async def runner():
            mw.before_agent(
                state={"messages": []},
                runtime=SimpleNamespace(thread_id="async-scope"),
            )
            await mw.aafter_model(
                state={
                    "messages": [
                        HumanMessage(content="q"),
                        AIMessage(content="a"),
                    ]
                },
                runtime=None,
            )

        asyncio.run(runner())

        ctx.add.assert_called_once()
        assert ctx.add.call_args.kwargs["scope"] == "async-scope"

    def test_awrap_tool_call_offloads_record(self) -> None:
        ctx = _fake_ctx()
        mw = ContextSeekMiddleware(ctx=ctx, scope="s", record_tool_calls=True)

        request = SimpleNamespace(
            tool=SimpleNamespace(name="search"),
            tool_call={"name": "search", "args": {"q": "x"}, "id": "tc-1"},
            state={
                "messages": [
                    HumanMessage(content="task"),
                    AIMessage(
                        content="reasoning",
                        tool_calls=[
                            {"name": "search", "args": {"q": "x"}, "id": "tc-1"}
                        ],
                    ),
                ]
            },
        )

        async def handler(_req):
            return ToolMessage(content="result body", tool_call_id="tc-1")

        async def runner():
            return await mw.awrap_tool_call(request, handler)

        result = asyncio.run(runner())

        assert isinstance(result, ToolMessage)
        assert result.content == "result body"
        ctx.add.assert_called_once()
        kwargs = ctx.add.call_args.kwargs
        assert kwargs["content"]["tool"] == "search"
        assert kwargs["content"]["result"] == "result body"
        assert kwargs["content"]["rationale"] == "reasoning"
        assert kwargs["content"]["task"] == "task"
        assert kwargs["scope"] == "s"
