"""Tests for the simplified retrieve/expand API and tool descriptors.

(Replaces former ContextInjectionBuilder tests after the API simplification.)
"""

from contextseek import (
    ResponseMeta,
    RetrieveResponse,
    ContextSeek,
    ToolSpec,
)
from contextseek.domain.tools import EXPAND_HINT, default_tool_specs


class TestRetrieveResponse:
    def test_iterable(self):
        ctx = ContextSeek()
        ctx.add("hello world", scope="t/p", source="cli")
        ctx.add("alpha beta", scope="t/p", source="cli")
        response = ctx.retrieve("hello", scope="t/p")
        assert isinstance(response, RetrieveResponse)
        assert len(response) >= 1
        ids = [hit.item.id for hit in response]
        assert all(isinstance(i, str) and i for i in ids)

    def test_meta_layer_full_when_no_summary(self):
        ctx = ContextSeek()
        ctx.add("plain content", scope="t/p", source="cli")
        response = ctx.retrieve("plain", scope="t/p")
        assert response.meta.layer == "full"
        assert response.meta.full_via == "expand"

    def test_full_flag_returns_full_layer(self):
        ctx = ContextSeek()
        ctx.add("some content", scope="t/p", source="cli")
        response = ctx.retrieve("content", scope="t/p", full=True)
        assert response.meta.layer == "full"
        for hit in response:
            assert hit.layer == "full"

    def test_tags_filter_requires_all_tags(self):
        ctx = ContextSeek()
        kept = ctx.add(
            "database backup runbook",
            scope="t/p",
            source="cli",
            tags=["ops", "database"],
        )
        other = ctx.add(
            "database onboarding guide",
            scope="t/p",
            source="cli",
            tags=["docs", "database"],
        )

        tagged_response = ctx.retrieve(
            "database", scope="t/p", tags=["ops", "database"]
        )
        unfiltered_response = ctx.retrieve("database", scope="t/p")

        assert [hit.item.id for hit in tagged_response] == [kept.id]
        assert {hit.item.id for hit in unfiltered_response} == {kept.id, other.id}


class TestExpand:
    def test_expand_returns_full_items_without_scope(self):
        ctx = ContextSeek()
        item = ctx.add("expand target", scope="t/p", source="cli")
        response = ctx.retrieve("expand", scope="t/p")
        full_items = ctx.expand(list(response))
        assert any(it.id == item.id for it in full_items)
        for it in full_items:
            assert it.content


class TestTools:
    def test_default_tool_specs_contains_retrieve_and_expand(self):
        specs = default_tool_specs()
        names = {s.name for s in specs}
        assert {"retrieve", "expand"} <= names

    def test_to_openai(self):
        spec = ToolSpec(
            name="x",
            description="d",
            parameters={"type": "object", "properties": {}},
        )
        out = spec.to_openai()
        assert out["type"] == "function"
        assert out["function"]["name"] == "x"
        assert out["function"]["description"] == "d"

    def test_to_anthropic(self):
        spec = ToolSpec(
            name="x",
            description="d",
            parameters={"type": "object", "properties": {}},
        )
        out = spec.to_anthropic()
        assert out["name"] == "x"
        assert out["description"] == "d"
        assert "input_schema" in out

    def test_expand_hint_used_in_meta(self):
        meta = ResponseMeta(layer="summary", full_via="expand", hint=EXPAND_HINT)
        assert "expand" in meta.hint
