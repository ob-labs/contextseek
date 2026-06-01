"""Materialize distilled prompt skills as SKILL.md files on disk.

The mirror operation of :mod:`contextseek.daemon.sync_cmd`: instead of
importing notes/documents *into* ContextSeek, this writes ``stage=skill``
items *out* to a directory of Hermes-style ``SKILL.md`` files. That directory
becomes a portable source other agent tools (Claude Code, Qoder, ...) can pick
up — directly or via a hub/symlink tool such as SkillForge.

Only ``skill_type="prompt"`` skills are materialized; tool/mcp skills are JSON
tool definitions, not SKILL.md documents, and are exported via
``ctx.skill_tools()`` instead.

IMPORTANT: the export directory must NOT also be a daemon ``WATCH_PATHS``
target. Watching it would re-ingest the emitted SKILL.md files as raw
documents, forming a feedback loop.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from contextseek.domain.skill_executor import SkillExporter

if TYPE_CHECKING:
    from contextseek.client.contextseek import ContextSeek

_MANIFEST_NAME = ".contextseek-export.json"


@dataclass
class ExportReport:
    written: int = 0
    unchanged: int = 0
    pruned: int = 0
    skipped_low_confidence: int = 0
    out_dir: str = ""


def _slugify(name: str, fallback: str) -> str:
    """Filesystem-safe slug: lowercase, non-alphanumeric → single dash."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or fallback


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_manifest(path: pathlib.Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def export_skills(
    ctx: "ContextSeek",
    *,
    scope: str,
    out_dir: str | pathlib.Path,
    min_confidence: float = 0.8,
    dry_run: bool = False,
    prune: bool = True,
) -> ExportReport:
    """Write prompt skills in *scope* to ``out_dir/<slug>/SKILL.md``.

    Idempotent: a SKILL.md whose content is unchanged is left untouched.
    A manifest (``out_dir/.contextseek-export.json``) records which directories
    this exporter owns, so pruning only removes our own stale exports and never
    touches hand-authored skills or symlinks placed in the same directory.
    """
    base = pathlib.Path(out_dir).expanduser()
    report = ExportReport(out_dir=str(base))
    exporter = SkillExporter()

    manifest_path = base / _MANIFEST_NAME
    old_manifest = _load_manifest(manifest_path)

    # Filter to confident prompt skills.
    skills = ctx.skills(scope, skill_type="prompt")
    selected = []
    for item in skills:
        if item.provenance.confidence < min_confidence:
            report.skipped_low_confidence += 1
            continue
        selected.append(item)

    # Assign collision-free slugs (append id8 when two skills share a slug).
    new_manifest: dict[str, dict[str, str]] = {}
    used_slugs: set[str] = set()
    for item in selected:
        name = item.content.get("name", "") if isinstance(item.content, dict) else ""
        slug = _slugify(name, fallback=f"skill_{item.id[:8]}")
        if slug in used_slugs:
            slug = f"{slug}-{item.id[:8]}"
        used_slugs.add(slug)

        md = exporter.to_hermes_skill_md(item)
        digest = _content_hash(md)
        new_manifest[item.id] = {"slug": slug, "hash": digest}

        skill_file = base / slug / "SKILL.md"
        prev = old_manifest.get(item.id)
        if (
            prev is not None
            and prev.get("slug") == slug
            and prev.get("hash") == digest
            and skill_file.exists()
        ):
            report.unchanged += 1
            continue

        if not dry_run:
            skill_file.parent.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(md, encoding="utf-8")
        report.written += 1

    # Prune directories we previously owned but no longer export.
    if prune:
        live_ids = set(new_manifest)
        for old_id, rec in old_manifest.items():
            if old_id in live_ids:
                continue
            stale_dir = base / rec.get("slug", "")
            if not rec.get("slug"):
                continue
            if not dry_run and stale_dir.is_dir():
                skill_md = stale_dir / "SKILL.md"
                skill_md.unlink(missing_ok=True)
                try:
                    stale_dir.rmdir()
                except OSError:
                    pass
            report.pruned += 1

    if not dry_run:
        base.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(new_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return report


__all__ = ["ExportReport", "export_skills"]
