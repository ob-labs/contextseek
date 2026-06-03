"""Tests for daemon/skill_export.py — materialize prompt skills as SKILL.md."""

from __future__ import annotations

import pathlib

from contextseek.daemon.skill_export import _MANIFEST_NAME, export_skills
from contextseek.domain.context_item import ContextItem, _generate_id
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.stages import Stage
from contextseek.plugs.skills import _parse_skill_md


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _skill(
    name: str, body: str = "do the thing", *, confidence: float = 0.8
) -> ContextItem:
    return ContextItem(
        id=_generate_id(),
        content={
            "skill_type": "prompt",
            "name": name,
            "description": f"{name} description",
            "version": "1.0.0",
            "tags": ["alpha"],
            "body": body,
        },
        scope="me/work",
        provenance=Provenance(
            source_type=SourceType.distillation,
            source_id="src",
            confidence=confidence,
        ),
        stage=Stage.skill,
    )


class _StubClient:
    """Minimal client: export_skills only calls .skills(scope, skill_type=...)."""

    def __init__(self, items: list[ContextItem]) -> None:
        self._items = items

    def skills(self, scope, *, skill_type=None, query=None, k=50):
        out = [it for it in self._items if it.scope == scope]
        if skill_type is not None:
            out = [
                it
                for it in out
                if isinstance(it.content, dict)
                and it.content.get("skill_type") == skill_type
            ]
        return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_writes_skill_md_with_frontmatter(tmp_path: pathlib.Path) -> None:
    ctx = _StubClient([_skill("Deploy Service", "step 1\nstep 2")])
    report = export_skills(ctx, scope="me/work", out_dir=tmp_path)

    assert report.written == 1
    md = (tmp_path / "deploy-service" / "SKILL.md").read_text(encoding="utf-8")
    assert md.startswith("---")
    assert "name: Deploy Service" in md
    assert "step 1" in md
    assert (tmp_path / _MANIFEST_NAME).exists()


def test_low_confidence_skipped(tmp_path: pathlib.Path) -> None:
    ctx = _StubClient(
        [
            _skill("Good", confidence=0.9),
            _skill("Draft", confidence=0.75),  # heuristic, below default 0.8
        ]
    )
    report = export_skills(ctx, scope="me/work", out_dir=tmp_path)

    assert report.written == 1
    assert report.skipped_low_confidence == 1
    assert (tmp_path / "good" / "SKILL.md").exists()
    assert not (tmp_path / "draft").exists()


def test_idempotent_second_run_unchanged(tmp_path: pathlib.Path) -> None:
    ctx = _StubClient([_skill("Deploy")])
    first = export_skills(ctx, scope="me/work", out_dir=tmp_path)
    second = export_skills(ctx, scope="me/work", out_dir=tmp_path)

    assert first.written == 1
    assert second.written == 0
    assert second.unchanged == 1


def test_prune_removes_stale_export(tmp_path: pathlib.Path) -> None:
    skill_a = _skill("Alpha")
    skill_b = _skill("Beta")
    export_skills(_StubClient([skill_a, skill_b]), scope="me/work", out_dir=tmp_path)
    assert (tmp_path / "beta" / "SKILL.md").exists()

    # Beta no longer present → its dir is pruned, alpha untouched.
    report = export_skills(_StubClient([skill_a]), scope="me/work", out_dir=tmp_path)
    assert report.pruned == 1
    assert (tmp_path / "alpha" / "SKILL.md").exists()
    assert not (tmp_path / "beta").exists()


def test_prune_leaves_unmanaged_dirs_untouched(tmp_path: pathlib.Path) -> None:
    # A hand-authored skill the exporter never wrote (not in manifest).
    (tmp_path / "handwritten").mkdir()
    (tmp_path / "handwritten" / "SKILL.md").write_text("manual", encoding="utf-8")

    export_skills(_StubClient([_skill("Alpha")]), scope="me/work", out_dir=tmp_path)
    # Remove alpha → prune our export, but never touch handwritten/.
    export_skills(_StubClient([]), scope="me/work", out_dir=tmp_path)

    assert (tmp_path / "handwritten" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "manual"
    assert not (tmp_path / "alpha").exists()


def test_dry_run_writes_nothing(tmp_path: pathlib.Path) -> None:
    ctx = _StubClient([_skill("Deploy")])
    report = export_skills(ctx, scope="me/work", out_dir=tmp_path, dry_run=True)

    assert report.written == 1
    assert not (tmp_path / "deploy").exists()
    assert not (tmp_path / _MANIFEST_NAME).exists()


def test_slug_collision_disambiguated(tmp_path: pathlib.Path) -> None:
    a = _skill("Same Name")
    b = _skill("Same Name")
    report = export_skills(_StubClient([a, b]), scope="me/work", out_dir=tmp_path)

    assert report.written == 2
    assert (tmp_path / "same-name" / "SKILL.md").exists()
    assert (tmp_path / f"same-name-{b.id[:8]}" / "SKILL.md").exists()


def test_roundtrip_through_hermes_parser(tmp_path: pathlib.Path) -> None:
    ctx = _StubClient([_skill("Deploy Service", "line one\n\nline two")])
    export_skills(ctx, scope="me/work", out_dir=tmp_path)

    md = (tmp_path / "deploy-service" / "SKILL.md").read_text(encoding="utf-8")
    parsed = _parse_skill_md(md)
    assert parsed["name"] == "Deploy Service"
    assert parsed["description"] == "Deploy Service description"
    assert "line one" in parsed["body"]
    assert "line two" in parsed["body"]
