"""Human-readable terminal renderer for `contextseek overview`.

Produces a styled ASCII dashboard showing skills, growth progress, and
accumulated item statistics.  No third-party dependencies required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from contextseek.domain.context_item import ContextItem
from contextseek.domain.results import EvolutionReport


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


def _skill_name(item: ContextItem) -> str:
    """Extract a display name from a skill item."""
    if isinstance(item.content, dict):
        name = item.content.get("name", "")
        if name and not name.startswith("skill_"):
            return name[:40]
        desc = item.content.get("description", "")
        if desc:
            return desc[:40]
    text = item.content_text.strip()
    return text[:40] if text else item.id[:12]


def _divider(label: str) -> str:
    dashes = _WIDTH - len(label) - 3
    return f"  {label}  " + "─" * max(0, dashes)


def render_overview(
    scope: str,
    skills: list[ContextItem],
    report: EvolutionReport,
    last_evolution: datetime | None,
    distill_threshold: int = 5,
    backend_label: str = "local",
    growing_items: list[ContextItem] | None = None,
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
            name = _skill_name(item)
            remaining = max(0, distill_threshold - item.access_count)
            lines.append(
                f"    {name:<48}  needs {remaining} more use{'s' if remaining != 1 else ''}"
            )
    else:
        lines.append("    Nothing nearing distillation yet.")

    lines.append("")

    # ── Accumulated ─────────────────────────────────────────────────────────
    lines.append(_divider("○ Accumulated"))
    total = report.total_items
    pending = report.pending_extraction + report.pending_convergence
    evolved_ago = _format_elapsed(last_evolution)
    lines.append(
        f"    {total} items  ·  {pending} pending evolution"
        f"  ·  last evolved {evolved_ago}"
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
