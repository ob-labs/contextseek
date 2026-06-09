"""ContextSeek × LangChain Agent Middleware.

Connects ContextSeek retrieval, storage, and evolution capabilities to a
LangChain ``create_agent()`` pipeline.

Augments the prompt only — does not control the agent flow or modify agent state.
Sidecars storage passively alongside the existing LangChain model and embedder.

Usage::

    from langchain.agents import create_agent
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from contextseek.bridges.langchain.middleware import ContextSeekMiddleware

    model = ChatOpenAI(model="gpt-4o")
    embedder = OpenAIEmbeddings(model="text-embedding-3-small")

    agent = create_agent(
        model=model,
        tools=[...],
        middleware=[
            ContextSeekMiddleware(
                model=model,
                embedder=embedder,
                retrieval_k=10,
                scope="my_project",
            ),
        ],
    )
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import weakref
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from contextseek.bridges.langchain._tracing import traceable
from contextseek.client.contextseek import ContextSeek

if TYPE_CHECKING:
    from contextseek.config.settings import EmbeddingSettings


# Per asyncio-task / per-thread scope. Set by ``before_agent`` and read by every
# downstream hook via ``_current_scope()``. Module-level so a single middleware
# instance can be safely shared across concurrent agent sessions.
_SCOPE_VAR: ContextVar[str | None] = ContextVar(
    "contextseek_middleware_scope", default=None
)


class ContextSeekMiddleware(
    AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]
):
    """LangChain ``AgentMiddleware`` that bridges ContextSeek into the agent loop.

    - ``wrap_model_call``: retrieve relevant context and append it to ``system_message``
    - ``after_model``: persist the latest assistant turn to ContextSeek
    - ``wrap_tool_call``: persist tool invocations (with rationale + task) for
      provenance — gated by ``record_tool_calls`` (default ``False``)
    - ``before_agent``: lazily resolve ``scope`` from ``runtime.thread_id``
    - ``after_agent``: throttled fire-and-forget ``ctx.compact()`` for evolution
    """

    def __init__(
        self,
        ctx: ContextSeek | None = None,
        *,
        model: BaseChatModel | str | None = None,
        embedder: Embeddings | None = None,
        retrieval_k: int = 10,
        retrieval_tags: list[str] | None = None,
        min_score: float | None = None,
        tool_arg_overrides: dict[str, dict[str, Any]] | None = None,
        auto_store: bool = True,
        record_tool_calls: bool = False,
        auto_compact: bool = False,
        compact_every: int = 20,
        scope: str | None = None,
    ) -> None:
        super().__init__()
        self.retrieval_k = retrieval_k
        self.retrieval_tags = list(retrieval_tags) if retrieval_tags else None
        self.min_score = min_score
        self.tool_arg_overrides = tool_arg_overrides or {}
        self.auto_store = auto_store
        # Whether wrap_tool_call persists each tool invocation to ContextSeek.
        # Independent of ``auto_store`` (which gates agent-response storage).
        # Tool calls fire far more often than final answers, and each recorded
        # call triggers an extra ctx.add() -> summarizer (L0/L1) + embed + DB
        # write. Defaults to ``False`` to avoid that volume; set ``True`` when
        # per-tool provenance is needed.
        self.record_tool_calls = record_tool_calls
        self.auto_compact = auto_compact
        self.compact_every = compact_every
        # Instance-level fallback scope. Real per-session scope lives in
        # ``_SCOPE_VAR``; this is only consulted when ``before_agent`` did not run.
        self._scope = scope

        self._compact_counters: dict[str, int] = {}
        self._compact_locks: dict[str, threading.Lock] = {}
        # Guards mutations of ``_compact_counters`` / ``_compact_locks`` against
        # concurrent ``after_agent`` calls (one per session thread).
        self._compact_state_lock = threading.Lock()
        # Single-worker pool: serializes compact across scopes; per-scope lock
        # additionally prevents same-scope re-entry.
        self._compact_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="contextseek-compact"
        )
        # Belt-and-suspenders: clean up the executor on GC / interpreter exit.
        # ``weakref.finalize`` does not pin the instance.
        weakref.finalize(self, self._compact_executor.shutdown, True)

        if ctx is not None:
            self.ctx = ctx
        else:
            self.ctx = self._build_contextseek(model, embedder)

    # ── construction ──────────────────────────────────────

    def _build_contextseek(
        self,
        model: BaseChatModel | str | None,
        embedder: Embeddings | None,
    ) -> ContextSeek:
        import dataclasses

        from contextseek.client.contextseek import ContextSeek as _ContextSeek
        from contextseek.config.factory import build_summarizer
        from contextseek.config.settings import ContextSeekSettings
        from contextseek.embedders.langchain_embedder import LangChainEmbedder

        settings = ContextSeekSettings()

        # Delegate backend selection (memory / seekdb / oceanbase / file),
        # embedder bridging (seekdb built-in ONNX), and resolver construction
        # entirely to the canonical factory so this middleware stays in sync
        # with any new backends added to contextseek.
        ctx = _ContextSeek.from_settings(settings)

        # Override embedder when the caller supplied a LangChain Embeddings
        # object — takes priority over both env-var config and seekdb built-in.
        if embedder is not None:
            dims = self._resolve_vector_dims(embedder, settings.embedding)
            ctx = dataclasses.replace(
                ctx, embedder=LangChainEmbedder(embedder, dims=dims)
            )

        # Override summarizer when the caller supplied a model — avoids
        # spinning up a second LLM instance when the agent already has one.
        if model is not None:
            if isinstance(model, str):
                from langchain.chat_models import init_chat_model

                model = init_chat_model(model)
            ctx = dataclasses.replace(
                ctx, summarizer=build_summarizer(settings.summarizer, llm=model)
            )

        return ctx

    @staticmethod
    def _resolve_vector_dims(
        embedder: Embeddings | None,
        embedding_settings: "EmbeddingSettings",
    ) -> int:
        """env EMBEDDING_DIMS > probe via embed_query > 1536 fallback."""
        if embedding_settings.dims and embedding_settings.dims > 0:
            return embedding_settings.dims
        if embedder is not None:
            try:
                return len(embedder.embed_query("test"))
            except Exception:
                pass
        return 1536

    # ── traced ctx wrappers ───────────────────────────────
    # Thin pass-throughs around the three ContextSeek calls the middleware
    # makes, decorated with ``@traceable`` so each shows up as a span (with
    # inputs/outputs) in LangSmith when ``LANGSMITH_TRACING=true``. Without
    # langsmith installed, ``traceable`` is a no-op (see ``_tracing``).
    #
    # Only forward plain-data args (query / content / scope / tags …) so the
    # captured inputs stay JSON-clean — ``self`` and other heavy objects are
    # not threaded through.

    @traceable(run_type="retriever", name="ContextSeek.retrieve")
    def _traced_retrieve(self, query: str, **kwargs: Any) -> Any:
        return self.ctx.retrieve(query, **kwargs)

    @traceable(run_type="tool", name="ContextSeek.add")
    def _traced_add(self, **kwargs: Any) -> Any:
        return self.ctx.add(**kwargs)

    @traceable(run_type="chain", name="ContextSeek.compact")
    def _traced_compact(self, *, scope: str, dry_run: bool = False) -> Any:
        return self.ctx.compact(scope=scope, dry_run=dry_run)

    # ── scope ─────────────────────────────────────────────

    def _current_scope(self) -> str:
        """Resolve the scope for the current call.

        - Constructor-provided ``scope=`` is per-instance lock-in: when the
          user passes one, it's THE scope for every session this instance
          handles, and the ContextVar is not consulted.
        - Otherwise (constructor scope is None), the per-session ContextVar
          set by ``before_agent`` wins, falling back to ``"default"``.
        """
        if self._scope:
            return self._scope
        return _SCOPE_VAR.get() or "default"

    # ── before_agent ──────────────────────────────────────

    @override
    def before_agent(self, state: AgentState[Any], runtime: Runtime[ContextT]) -> None:
        scope = self._scope or getattr(runtime, "thread_id", None) or "default"
        # Set the per-session scope. Do NOT mutate ``self._scope`` — the
        # instance is shared across concurrent sessions.
        _SCOPE_VAR.set(scope)
        return None

    # ── wrap_model_call ───────────────────────────────────

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        query = self._last_user_text(request.messages)
        if not query:
            return handler(request)
        try:
            response = self._traced_retrieve(
                query,
                scope=self._current_scope(),
                k=self.retrieval_k,
                tags=self.retrieval_tags,
                min_score=self.min_score,
            )
        except Exception:
            return handler(request)
        if not response.items:
            return handler(request)
        context_block = self._format_context_block(response)
        new_sys = self._append_to_system(request.system_message, context_block)
        return handler(request.override(system_message=new_sys))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[
            [ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]
        ],
    ) -> ModelResponse[ResponseT]:
        query = self._last_user_text(request.messages)
        if not query:
            return await handler(request)
        try:
            response = await asyncio.to_thread(
                self._traced_retrieve,
                query,
                scope=self._current_scope(),
                k=self.retrieval_k,
                tags=self.retrieval_tags,
                min_score=self.min_score,
            )
        except Exception:
            return await handler(request)
        if not response.items:
            return await handler(request)
        context_block = self._format_context_block(response)
        new_sys = self._append_to_system(request.system_message, context_block)
        return await handler(request.override(system_message=new_sys))

    # ── after_model ───────────────────────────────────────

    @override
    def after_model(self, state: AgentState[Any], runtime: Runtime[ContextT]) -> None:
        if not self.auto_store:
            return None
        messages = state["messages"]
        last_ai = self._last_ai_message(messages)
        last_user = self._last_user_text(messages)
        # Only persist when we have BOTH a user turn and an AI reply — an AI
        # message without a preceding user turn (e.g. agent greeting) lacks
        # the question half and would degrade retrieval relevance later.
        if last_ai is None or not last_user:
            return None
        # Skip intermediate tool-calling steps: when the AI message carries
        # tool_calls it is not a final answer — the actual tool artefacts are
        # recorded by wrap_tool_call, so storing this here would create noise.
        if getattr(last_ai, "tool_calls", None):
            return None
        try:
            from contextseek.domain.provenance import SourceType

            ai_text = (
                last_ai.content
                if isinstance(last_ai.content, str)
                else str(last_ai.content)
            )
            if not ai_text:
                return None
            # Store the Q+A pair so the Summarizer inside ctx.add() has full
            # context to produce a tight abstract (L2) and overview (L1).
            # Distillation is the Summarizer's job, not the middleware's.
            self._traced_add(
                content=f"Q: {last_user}\nA: {ai_text}",
                scope=self._current_scope(),
                source="agent_response",
                source_type=SourceType.agent_inference,
                tags=["langchain", "agent_response"],
            )
        except Exception:
            pass
        return None

    @override
    async def aafter_model(
        self, state: AgentState[Any], runtime: Runtime[ContextT]
    ) -> None:
        # ctx.add() runs sync LLM summarizer + DB IO; offload to a thread so
        # the asyncio event loop is not blocked. ``asyncio.to_thread`` copies
        # the current contextvars, so ``_SCOPE_VAR`` is visible inside.
        await asyncio.to_thread(self.after_model, state, runtime)
        return None

    # ── wrap_tool_call ────────────────────────────────────

    @override
    def wrap_tool_call(self, request: Any, handler: Callable[..., Any]) -> Any:
        tool_name, tool_args, tool_call_id = self._extract_tool_call_fields(request)
        # Apply arg overrides regardless of record_tool_calls — they affect
        # tool *execution*, not recording.
        tool_args = self._apply_tool_arg_overrides(request, tool_name, tool_args)
        if not self.record_tool_calls:
            return handler(request)
        messages = self._messages_from_request(request)
        rationale = self._reasoning_for_tool_call(messages, tool_call_id)
        task = self._last_user_text(messages)

        result = handler(request)
        self._record_tool_call(
            tool_name=tool_name,
            tool_args=tool_args,
            result=result,
            rationale=rationale,
            task=task,
        )
        return result

    @override
    async def awrap_tool_call(
        self, request: Any, handler: Callable[..., Awaitable[Any]]
    ) -> Any:
        # Read state BEFORE awaiting handler — by the time we resume, the
        # state may have advanced beyond this tool call.
        tool_name, tool_args, tool_call_id = self._extract_tool_call_fields(request)
        # Apply arg overrides regardless of record_tool_calls — they affect
        # tool *execution*, not recording.
        tool_args = self._apply_tool_arg_overrides(request, tool_name, tool_args)
        if not self.record_tool_calls:
            return await handler(request)
        messages = self._messages_from_request(request)
        rationale = self._reasoning_for_tool_call(messages, tool_call_id)
        task = self._last_user_text(messages)

        result = await handler(request)
        # Offload sync ctx.add() to a thread so the event loop is not blocked.
        await asyncio.to_thread(
            self._record_tool_call,
            tool_name=tool_name,
            tool_args=tool_args,
            result=result,
            rationale=rationale,
            task=task,
        )
        return result

    def _record_tool_call(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        result: Any,
        rationale: str | None,
        task: str | None,
    ) -> None:
        try:
            from contextseek.domain.provenance import SourceType

            result_text = self._stringify_tool_result(result)
            self._traced_add(
                content={
                    "tool": tool_name,
                    "args": tool_args,
                    "result": result_text,
                    "rationale": rationale,
                    "task": task,
                },
                scope=self._current_scope(),
                source="tool_execution",
                source_type=SourceType.trace_extraction,
                tags=["langchain", "tool", tool_name],
            )
        except Exception:
            pass

    @staticmethod
    def _extract_tool_call_fields(
        request: Any,
    ) -> tuple[str, dict[str, Any], str | None]:
        tool_call = getattr(request, "tool_call", None) or {}
        tool = getattr(request, "tool", None)

        name = (
            getattr(tool, "name", None)
            or (tool_call.get("name") if isinstance(tool_call, dict) else None)
            or "unknown_tool"
        )
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        call_id = tool_call.get("id") if isinstance(tool_call, dict) else None
        return name, args, call_id

    @staticmethod
    def _messages_from_request(request: Any) -> list:
        state = getattr(request, "state", None)
        if isinstance(state, dict):
            return state.get("messages", []) or []
        return []

    @staticmethod
    def _stringify_tool_result(result: Any) -> str:
        """ToolMessage / list / Command → str (JSON-serializable)."""
        if result is None:
            return ""
        content = getattr(result, "content", None)
        if content is None:
            return str(result)
        if isinstance(content, str):
            return content
        return str(content)

    def _apply_tool_arg_overrides(
        self, request: Any, tool_name: str, tool_args: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply configured per-tool argument overrides before execution."""
        if not self.tool_arg_overrides:
            return tool_args
        overrides = self.tool_arg_overrides.get(tool_name)
        if not overrides:
            return tool_args
        merged = {**tool_args, **overrides}
        tool_call = getattr(request, "tool_call", None)
        if isinstance(tool_call, dict):
            current_args = tool_call.get("args")
            if isinstance(current_args, dict):
                current_args.update(overrides)
            else:
                tool_call["args"] = merged
        return merged

    # ── after_agent ───────────────────────────────────────

    @override
    def after_agent(self, state: AgentState[Any], runtime: Runtime[ContextT]) -> None:
        if not self.auto_compact or self.compact_every <= 0:
            return None
        scope = self._current_scope()
        # Atomic read-modify-write under the state lock so concurrent
        # ``after_agent`` calls don't lose increments.
        with self._compact_state_lock:
            self._compact_counters[scope] = self._compact_counters.get(scope, 0) + 1
            if self._compact_counters[scope] < self.compact_every:
                return None
            self._compact_counters[scope] = 0
        self._fire_compact(scope)
        return None

    @override
    async def aafter_agent(
        self, state: AgentState[Any], runtime: Runtime[ContextT]
    ) -> None:
        # ``_fire_compact`` is already non-blocking (submits to executor),
        # so the sync version is safe to call from an async context.
        return self.after_agent(state, runtime)

    def _fire_compact(self, scope: str) -> None:
        """Submit a compact task to the bounded executor.

        Per-scope non-blocking lock — if a previous compact is still running
        for this scope, the current trigger is skipped (avoids pile-up).
        """
        with self._compact_state_lock:
            lock = self._compact_locks.setdefault(scope, threading.Lock())
        if not lock.acquire(blocking=False):
            return

        def _run() -> None:
            try:
                self._traced_compact(scope=scope, dry_run=False)
            except Exception:
                pass
            finally:
                lock.release()

        try:
            self._compact_executor.submit(_run)
        except RuntimeError:
            # Executor already shut down — drop the task and release the lock.
            lock.release()

    def shutdown(self, wait: bool = True) -> None:
        """Stop accepting new compact tasks; optionally wait for in-flight to finish.

        Call from a graceful service shutdown hook (e.g. FastAPI lifespan
        exit). Idempotent.
        """
        self._compact_executor.shutdown(wait=wait)

    # ── helpers ───────────────────────────────────────────

    @staticmethod
    def _last_user_text(messages: list) -> str | None:
        for m in reversed(messages):
            if isinstance(m, HumanMessage) and m.content:
                return m.content if isinstance(m.content, str) else str(m.content)
        return None

    @staticmethod
    def _last_ai_message(messages: list) -> AIMessage | None:
        for m in reversed(messages):
            if isinstance(m, AIMessage):
                return m
        return None

    @staticmethod
    def _reasoning_for_tool_call(
        messages: list, tool_call_id: str | None, max_chars: int = 2000
    ) -> str | None:
        """Reverse-scan for the AIMessage that emitted ``tool_call_id`` and return its content."""
        if not tool_call_id:
            return None
        for m in reversed(messages):
            if not isinstance(m, AIMessage):
                continue
            for tc in m.tool_calls or []:
                if isinstance(tc, dict) and tc.get("id") == tool_call_id:
                    content = (
                        m.content if isinstance(m.content, str) else str(m.content)
                    )
                    return content[:max_chars] if content else None
        return None

    @staticmethod
    def _format_context_block(response: Any) -> str:
        lines = [
            "\n[Relevant Context - Prior Session Policy Lessons]",
            (
                "Use these as mandatory policy overrides learned from prior "
                "successful runs. If a lesson provides parameter values, "
                "those values override tool defaults and should be treated as required."
            ),
        ]
        for hit in response.items:
            line = hit.item.summary or hit.item.abstract or ""
            if line:
                lines.append(f"- {line}")
        return "\n".join(lines)

    @staticmethod
    def _append_to_system(
        system_message: SystemMessage | None, context_block: str
    ) -> SystemMessage:
        if system_message is None:
            return SystemMessage(content=context_block)
        content = system_message.content
        if isinstance(content, str):
            return SystemMessage(content=content + "\n" + context_block)
        return SystemMessage(
            content=[*content, {"type": "text", "text": context_block}]
        )


__all__ = ["ContextSeekMiddleware"]
