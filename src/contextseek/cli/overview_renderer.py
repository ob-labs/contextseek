"""Human-readable terminal renderer for `contextseek overview`.

Produces a styled ASCII dashboard showing skills, growth progress, and
accumulated item statistics.  No third-party dependencies required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from contextseek.domain.context_item import ContextItem
from contextseek.domain.results import EvolutionReport
from contextseek.domain.stages import Stage

if TYPE_CHECKING:
    from contextseek.evolution.lint import LintReport


_BLOCK_FULL = "█"
_BLOCK_HALF = "░"
_WIDTH = 70


def _confidence_bar(value: float, width: int = 5) -> str:
    """Render a block progress bar for a confidence value in [0, 1]."""
    filled = round(value * width)
    filled = max(0, min(width, filled))
    return _BLOCK_FULL * filled + _BLOCK_HALF * (width - filled)


def _format_elapsed(dt: datetime | None) -> str:
    """Render a datetime as a human-readable 'N ago' string."""
    if dt is None:
        return "never"
    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = seconds // 86400
    return f"{d} day{'s' if d != 1 else ''} ago"


def _item_display_label(item: ContextItem, *, max_len: int = 48) -> str:
    """Short human-readable label (path, title, or first line — not raw dict repr)."""
    prov = item.provenance
    if prov and prov.source_id:
        src = prov.source_id.replace("\\", "/").rstrip("/")
        if src:
            leaf = src.split("/")[-1]
            if leaf and not leaf.startswith("skill_"):
                return leaf[:max_len]

    for field in (item.summary, item.abstract):
        if isinstance(field, str) and field.strip():
            line = field.strip().splitlines()[0].strip().lstrip("#-* ").strip()
            if line:
                return line[:max_len]

    if isinstance(item.content, str):
        for line in item.content.splitlines():
            line = line.strip()
            if line and not line.startswith("```"):
                return line[:max_len]

    if isinstance(item.content, dict):
        for key in ("name", "title", "path", "source", "description", "body"):
            val = item.content.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:max_len]

    return f"#{item.id[:8]}"


def _skill_name(item: ContextItem) -> str:
    """Extract a display name from a skill item."""
    if isinstance(item.content, dict):
        name = item.content.get("name", "")
        if name and not name.startswith("skill_"):
            return name[:40]
        desc = item.content.get("description", "")
        if desc:
            return desc[:40]
    return _item_display_label(item, max_len=40)


def _divider(label: str) -> str:
    dashes = _WIDTH - len(label) - 3
    return f"  {label}  " + "─" * max(0, dashes)


def _next_evolution_hint(
    *,
    total: int,
    ready_to_distill: int,
    warming: int,
    pending: int,
    skills: int,
    scope: str,
) -> str:
    if total == 0:
        return f"contextseek sync ~/notes --scope {scope}"
    if pending > 0:
        return f"contextseek compact --scope {scope} --dry-run"
    if ready_to_distill > 0:
        return f"contextseek compact --scope {scope}"
    if warming > 0:
        return "keep retrieving these items until they reach the skill threshold"
    if skills == 0:
        return 'contextseek retrieve --query "..." then contextseek feedback --item-id <id> --score 1'
    return f"contextseek daemon status  # check automatic evolution"


def render_overview(
    scope: str,
    skills: list[ContextItem],
    report: EvolutionReport,
    last_evolution: datetime | None,
    distill_threshold: int = 5,
    backend_label: str = "local",
    growing_items: list[ContextItem] | None = None,
    all_items: list[ContextItem] | None = None,
    lint_report: "LintReport | None" = None,
) -> str:
    """Render a human-readable overview dashboard.

    Args:
        scope: The scope being displayed.
        skills: Items with stage=skill.
        report: EvolutionReport from ctx.overview().
        last_evolution: Timestamp of the last lifecycle run (or None).
        distill_threshold: Access count needed to distill a skill.
        backend_label: Short label for the storage backend.
        growing_items: Knowledge/extracted items approaching distillation.
        all_items: Active items in the scope, used for evolution readiness stats.
        lint_report: Optional lint findings to show in the "Needs Review" section.

    Returns:
        Multi-line string suitable for direct print().
    """
    lines: list[str] = []

    lines.append("")
    lines.append(
        f"  ContextSeek · {scope}  ({backend_label})"
    )
    lines.append("")

    # ── Skills ──────────────────────────────────────────────────────────────
    lines.append(_divider("❆ Your Skills"))
    if skills:
        for item in skills[:10]:
            name = _skill_name(item)
            uses = item.access_count
            conf = item.effective_confidence or item.provenance.confidence
            bar = _confidence_bar(conf)
            lines.append(
                f"    {name:<38}  {uses:>3} uses  ·  {bar}  {conf:.2f}"
            )
    else:
        lines.append("    No skills yet.  Keep using ContextSeek — they will emerge automatically.")

    lines.append("")

    # ── Growing ──────────────────────────────────────────────────────────────
    lines.append(_divider("◎ Growing"))
    growing = growing_items or []
    if growing:
        for item in growing[:5]:
            name = _item_display_label(item)
            remaining = max(0, distill_threshold - item.access_count)
            lines.append(
                f"    {name:<48}  needs {remaining} more use{'s' if remaining != 1 else ''}"
            )
    else:
        lines.append("    Nothing nearing distillation yet.")

    lines.append("")

    # ── Evolution ───────────────────────────────────────────────────────────
    items = [it for it in (all_items or []) if not it.is_deleted]
    raw_count = sum(1 for it in items if it.stage == Stage.raw)
    extracted_count = sum(1 for it in items if it.stage == Stage.extracted)
    knowledge_items = [it for it in items if it.stage == Stage.knowledge]
    ready_to_distill = sum(
        1 for it in knowledge_items if it.access_count >= distill_threshold
    )
    warming = sum(1 for it in knowledge_items if 3 <= it.access_count < distill_threshold)
    pending = report.pending_extraction + report.pending_convergence
    evolved_ago = _format_elapsed(last_evolution)
    next_hint = _next_evolution_hint(
        total=report.total_items,
        ready_to_distill=ready_to_distill,
        warming=warming,
        pending=pending,
        skills=len(skills),
        scope=scope,
    )

    lines.append(_divider("✦ Evolution"))
    lines.append(f"    raw → extracted        {report.pending_extraction} ready")
    lines.append(f"    extracted → knowledge  {report.pending_convergence} ready")
    lines.append(
        f"    knowledge → skill      {ready_to_distill} ready"
        f" · {warming} warming"
    )
    lines.append(f"    recent activity        last evolved {evolved_ago}")
    if raw_count or extracted_count:
        lines.append(f"    current inputs         raw={raw_count} extracted={extracted_count}")
    lines.append(f"    next                   {next_hint}")
    lines.append("")

    # ── Quick health hints (full item-level details live in contextseek lint) ─
    if lint_report and (lint_report.orphans or lint_report.distill_opportunities):
        lines.append(_divider("? Needs Review"))
        if lint_report.orphans:
            lines.append(
                "    Some knowledge is not linked to any skill yet."
                "  This is normal after a fresh import."
            )
        if lint_report.distill_opportunities:
            count = len(lint_report.distill_opportunities)
            lines.append(
                f"    {count} item{'s' if count != 1 else ''} may be ready"
                " to distill into skills."
            )
        lines.append(
            f"    └─ contextseek lint --scope {scope}  for item-level details"
        )
        lines.append("")

    # ── Accumulated ─────────────────────────────────────────────────────────
    lines.append(_divider("○ Accumulated"))
    total = report.total_items
    pending = report.pending_extraction + report.pending_convergence
    evolved_ago = _format_elapsed(last_evolution)
    distill_note = ""
    if report.distill_candidates:
        distill_note = f"  ·  {report.distill_candidates} ready to distill"
    lines.append(
        f"    {total} items  ·  {pending} pending evolution"
        f"  ·  last evolved {evolved_ago}{distill_note}"
    )
    dist = report.stage_distribution
    if dist:
        parts = [f"{k}: {v}" for k, v in sorted(dist.items())]
        lines.append(f"    stages — {', '.join(parts)}")

    lines.append("")

    # ── Hint ────────────────────────────────────────────────────────────────
    if not skills:
        lines.append(
            "  Tip: Connect MCP to let ContextSeek inject skills into Claude/Cursor."
        )
        lines.append("       Run `contextseek init` to get started.")
        lines.append("")

    return "\n".join(lines)
