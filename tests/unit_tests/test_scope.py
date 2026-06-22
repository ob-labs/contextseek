"""Tests for ScopeBuilder, ScopeTemplates, ScopeTree, ScopeStats, and lint."""

import warnings

import pytest

from contextseek.scope import (
    ScopeBuilder,
    ScopeLintWarning,
    ScopeTemplates,
    _lint_scope,
)


class TestScopeBuilder:
    def test_basic_chain(self):
        scope = (
            ScopeBuilder()
            .org("acme")
            .project("payment-service")
            .agent("refund-agent")
            .build()
        )
        assert scope == "acme/payment-service/refund-agent"

    def test_run_adds_type_label(self):
        scope = ScopeBuilder().org("acme").project("payment").run("run_001").build()
        assert scope == "acme/payment/run/run_001"

    def test_task_adds_type_label(self):
        scope = ScopeBuilder().org("acme").task("t-42").build()
        assert scope == "acme/task/t-42"

    def test_user_adds_type_label(self):
        scope = ScopeBuilder().user("u-99").domain("notes").build()
        assert scope == "user/u-99/notes"

    def test_team_and_domain(self):
        scope = ScopeBuilder().org("acme").team("platform").domain("billing").build()
        assert scope == "acme/platform/billing"

    def test_append_raw_segment(self):
        scope = ScopeBuilder().org("acme").append("custom-segment").build()
        assert scope == "acme/custom-segment"

    def test_strips_slashes_from_segments(self):
        scope = ScopeBuilder().org("/acme/").project("/pay/").build()
        assert scope == "acme/pay"

    def test_immutable_chain(self):
        base = ScopeBuilder().org("acme")
        branch_a = base.project("pay")
        branch_b = base.project("auth")
        assert branch_a.build() == "acme/pay"
        assert branch_b.build() == "acme/auth"

    def test_build_raises_without_segments(self):
        with pytest.raises(ValueError, match="no segments"):
            ScopeBuilder().build()

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("SVC_NAME", "orders")
        monkeypatch.setenv("RUN_ID", "r-001")
        scope = ScopeBuilder.from_env(
            "acme",
            env_vars={"project": "SVC_NAME", "run": "RUN_ID"},
        ).build()
        assert scope == "acme/orders/run/r-001"

    def test_from_env_skips_missing_vars(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        scope = ScopeBuilder.from_env(
            "acme", env_vars={"project": "MISSING_VAR"}
        ).build()
        assert scope == "acme"

    def test_from_env_invalid_method_raises(self, monkeypatch):
        monkeypatch.setenv("X", "value")
        with pytest.raises(ValueError, match="no segment method 'bad_method'"):
            ScopeBuilder.from_env("acme", env_vars={"bad_method": "X"}).build()


class TestScopeTemplates:
    def test_org_knowledge(self):
        s = ScopeTemplates.org_knowledge("acme", "platform", "billing")
        assert s == "acme/platform/knowledge/billing"

    def test_agent_run_without_task(self):
        s = ScopeTemplates.agent_run("refund-agent", "r-001")
        assert s == "agent/refund-agent/run/r-001"

    def test_agent_run_with_task(self):
        s = ScopeTemplates.agent_run("refund-agent", "r-001", task_id="t-42")
        assert s == "agent/refund-agent/run/r-001/task/t-42"

    def test_user_space(self):
        s = ScopeTemplates.user_space("u-99", "notes")
        assert s == "user/u-99/notes"

    def test_shared_knowledge(self):
        s = ScopeTemplates.shared("my-project", "knowledge")
        assert s == "shared/my-project/knowledge"

    def test_shared_skill(self):
        s = ScopeTemplates.shared("my-project", "skill")
        assert s == "shared/my-project/skill"


class TestLint:
    def test_empty_scope_flagged(self):
        issues = _lint_scope("")
        assert issues

    def test_flat_scope_flagged(self):
        issues = _lint_scope("acme")
        assert any("/" in m for m in issues)

    def test_too_deep_flagged(self):
        deep = "/".join(["a"] * 7)
        issues = _lint_scope(deep)
        assert any("deep" in m for m in issues)

    def test_uppercase_flagged(self):
        issues = _lint_scope("Acme/Pay")
        assert any("uppercase" in m or "lowercase" in m for m in issues)

    def test_space_flagged(self):
        issues = _lint_scope("acme/my project")
        assert issues

    def test_empty_segment_flagged(self):
        issues = _lint_scope("acme//pay")
        assert any("empty" in m or "repeated" in m for m in issues)

    def test_clean_scope_has_no_issues(self):
        assert _lint_scope("acme/payment-service/agent") == []


class TestScopeLintInCtx:
    def test_lint_warning_emitted_on_add(self):
        from contextseek.client.contextseek import ContextSeek

        ctx = ContextSeek(_scope_lint=True)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ctx.add("hello", scope="flat", source="test")

        lint_warnings = [w for w in caught if issubclass(w.category, ScopeLintWarning)]
        assert lint_warnings, "expected at least one ScopeLintWarning for flat scope"

    def test_no_lint_warning_by_default(self):
        from contextseek.client.contextseek import ContextSeek

        ctx = ContextSeek()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ctx.add("hello", scope="flat", source="test")

        lint_warnings = [w for w in caught if issubclass(w.category, ScopeLintWarning)]
        assert not lint_warnings

    def test_no_warning_for_good_scope(self):
        from contextseek.client.contextseek import ContextSeek

        ctx = ContextSeek(_scope_lint=True)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ctx.add("hello", scope="acme/payment/agent", source="test")

        lint_warnings = [w for w in caught if issubclass(w.category, ScopeLintWarning)]
        assert not lint_warnings


class TestScopeTreeAndStats:
    def _make_ctx(self):
        from contextseek.client.contextseek import ContextSeek

        ctx = ContextSeek()
        ctx.add("raw content", scope="acme/pay/agent", source="test")
        ctx.add("more content", scope="acme/pay/agent", source="test")
        ctx.add("auth content", scope="acme/auth/agent", source="test")
        return ctx

    def test_scope_stats_item_count(self):
        ctx = self._make_ctx()
        stats = ctx.scope_stats("acme/pay/agent")
        assert stats.item_count == 2
        assert stats.scope == "acme/pay/agent"

    def test_scope_stats_stage_distribution(self):
        ctx = self._make_ctx()
        stats = ctx.scope_stats("acme/pay/agent")
        assert sum(stats.stage_distribution.values()) == stats.item_count

    def test_scope_stats_empty_scope(self):
        from contextseek.client.contextseek import ContextSeek

        ctx = ContextSeek()
        stats = ctx.scope_stats("nonexistent/scope")
        assert stats.item_count == 0
        assert stats.avg_confidence == 0.0
        assert stats.last_write is None

    def test_scope_tree_contains_scopes(self):
        ctx = self._make_ctx()
        tree = ctx.scope_tree()
        assert tree.nodes  # not empty

    def test_scope_tree_with_root(self):
        ctx = self._make_ctx()
        tree = ctx.scope_tree(root="acme")
        assert tree.nodes

    def test_scope_tree_print_runs(self, capsys):
        ctx = self._make_ctx()
        tree = ctx.scope_tree()
        tree.print()
        out = capsys.readouterr().out
        assert out  # something was printed
