"""Background lifecycle scheduler for automated evolution and compaction."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from contextseek.client.contextseek import ContextSeek


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LifecycleEvent:
    """Record of one automatic lifecycle execution."""

    scope: str
    action: str
    timestamp: datetime
    result: dict[str, Any]


@dataclass
class LifecycleScheduler:
    """Periodically run compact/evolution for registered scopes.

    Runs in a daemon thread at a configurable interval.

    Usage::
        scheduler = LifecycleScheduler(client=ctx, interval_seconds=3600)
        scheduler.register_scope("acme/bot/global")
        scheduler.start()
    """

    client: "ContextSeek"
    interval_seconds: float = 3600.0
    on_event: Callable[[LifecycleEvent], None] | None = None
    snapshot_dir: Any | None = None
    snapshot_keep: int = 7
    export_dir: Any | None = None
    export_min_confidence: float = 0.8
    _scopes: list[str] = field(default_factory=list, repr=False)
    _timer: threading.Timer | None = field(default=None, repr=False)
    _running: bool = field(default=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _history: list[LifecycleEvent] = field(default_factory=list, repr=False)

    def register_scope(self, scope: str) -> None:
        with self._lock:
            if scope not in self._scopes:
                self._scopes.append(scope)

    def unregister_scope(self, scope: str) -> None:
        with self._lock:
            self._scopes = [s for s in self._scopes if s != scope]

    @property
    def scopes(self) -> list[str]:
        with self._lock:
            return list(self._scopes)

    @property
    def history(self) -> list[LifecycleEvent]:
        return list(self._history)

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._schedule_next()

    def stop(self) -> None:
        self._running = False
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def run_once(self) -> list[LifecycleEvent]:
        """Execute one lifecycle cycle synchronously (for testing)."""
        return self._execute_cycle()

    def _schedule_next(self) -> None:
        if not self._running:
            return
        with self._lock:
            self._timer = threading.Timer(self.interval_seconds, self._tick)
            self._timer.daemon = True
            self._timer.start()

    def _tick(self) -> None:
        if not self._running:
            return
        self._execute_cycle()
        self._schedule_next()

    def _execute_cycle(self) -> list[LifecycleEvent]:
        events: list[LifecycleEvent] = []
        with self._lock:
            scopes = list(self._scopes)
        # Snapshot durable items before any irreversible compact/dream mutation.
        self._snapshot(scopes)
        for scope in scopes:
            # Phase 1: Apply decay
            decay_event = self._decay_scope(scope)
            if decay_event is not None:
                events.append(decay_event)
            # Phase 2: Compact (evolution auto-trigger)
            compact_event = self._compact_scope(scope)
            if compact_event is not None:
                events.append(compact_event)
            # Phase 3: Lint (diagnose) — results feed into dream target selection
            lint_report, lint_event = self._lint_scope(scope)
            if lint_event is not None:
                events.append(lint_event)
            # Phase 4: Dream (consolidation + divergence), guided by lint hints
            dream_event = self._dream_scope(scope, lint_report=lint_report)
            if dream_event is not None:
                events.append(dream_event)
            # Phase 5: Materialize distilled prompt skills as SKILL.md files
            if self.export_dir:
                export_event = self._export_scope(scope)
                if export_event is not None:
                    events.append(export_event)
        return events

    def _snapshot(self, scopes: list[str]) -> None:
        """Write a pre-cycle snapshot of durable items when a dir is configured."""
        if not self.snapshot_dir or not scopes:
            return
        try:
            from contextseek.daemon.snapshot import write_snapshot

            path = write_snapshot(
                self.client, scopes, self.snapshot_dir, keep=self.snapshot_keep
            )
            if path is not None:
                event = LifecycleEvent(
                    scope=",".join(scopes),
                    action="snapshot",
                    timestamp=_utc_now(),
                    result={"path": str(path)},
                )
                self._history.append(event)
                if self.on_event:
                    self.on_event(event)
        except Exception:  # noqa: BLE001 — snapshots must never block evolution
            pass

    def _decay_scope(self, scope: str) -> LifecycleEvent | None:
        """Apply decay policy to all items in a scope."""
        try:
            from contextseek.policies.decay import DecayConfig, apply_decay

            items_with_refs = self.client._list_items(scope)
            items = [item for _, item in items_with_refs]

            if not items:
                return None

            config = DecayConfig(
                half_life_days=7.0,
                ephemeral_ttl_seconds=3600.0,
            )
            result = apply_decay(items, config=config)

            # Write back decayed items
            if result.decayed_count > 0 or result.archived_count > 0:
                from contextseek.domain.serialization import serialize_context_item

                for ref, item in items_with_refs:
                    self.client.adapter.write(ref, serialize_context_item(item))

            event = LifecycleEvent(
                scope=scope,
                action="decay",
                timestamp=_utc_now(),
                result={
                    "decayed_count": result.decayed_count,
                    "archived_count": result.archived_count,
                },
            )
            self._history.append(event)
            if self.on_event:
                self.on_event(event)
            return event
        except Exception as exc:  # noqa: BLE001
            event = LifecycleEvent(
                scope=scope,
                action="decay_error",
                timestamp=_utc_now(),
                result={"error": str(exc)},
            )
            self._history.append(event)
            if self.on_event:
                self.on_event(event)
            return event

    def _compact_scope(self, scope: str) -> LifecycleEvent | None:
        try:
            result = self.client.compact(scope=scope, dry_run=False)
            event = LifecycleEvent(
                scope=scope,
                action="compact",
                timestamp=_utc_now(),
                result=result.__dict__
                if hasattr(result, "__dict__")
                else {"details": str(result)},
            )
            self._history.append(event)
            if self.on_event:
                self.on_event(event)
            return event
        except Exception as exc:  # noqa: BLE001
            event = LifecycleEvent(
                scope=scope,
                action="compact_error",
                timestamp=_utc_now(),
                result={"error": str(exc)},
            )
            self._history.append(event)
            if self.on_event:
                self.on_event(event)
            return event

    def _lint_scope(
        self, scope: str
    ) -> "tuple[Any | None, LifecycleEvent | None]":
        """Run lint health check on a scope and return (LintReport, event)."""
        try:
            from contextseek.evolution.lint import run_lint

            items_with_refs = self.client._list_items(scope)
            items = [item for _, item in items_with_refs]
            if not items:
                return None, None

            report = run_lint(items, scope=scope)
            event = LifecycleEvent(
                scope=scope,
                action="lint",
                timestamp=_utc_now(),
                result=report.to_dict(),
            )
            self._history.append(event)
            if self.on_event:
                self.on_event(event)
            return report, event
        except Exception as exc:  # noqa: BLE001
            event = LifecycleEvent(
                scope=scope,
                action="lint_error",
                timestamp=_utc_now(),
                result={"error": str(exc)},
            )
            self._history.append(event)
            if self.on_event:
                self.on_event(event)
            return None, event

    def _dream_scope(
        self, scope: str, *, lint_report: "Any | None" = None
    ) -> LifecycleEvent | None:
        """Run dreaming (consolidation + divergence) on a scope.

        When ``lint_report`` is provided its consolidation hints are used to
        select dream targets, making the dream cycle goal-directed rather than
        purely time-window based.
        """
        try:
            from contextseek.config.strategies import DreamStrategy
            from contextseek.evolution.dreaming import DreamEngine, pick_dream_targets

            # Check if dreaming is enabled via strategy
            dream_strategy = DreamStrategy()
            if hasattr(self.client, "strategy") and self.client.strategy:
                dream_strategy = self.client.strategy.dream

            if not dream_strategy.enabled:
                return None

            items_with_refs = self.client._list_items(scope)
            items = [item for _, item in items_with_refs]

            if not items:
                return None

            # Build graph-structure-driven targets from lint hints (or fallback)
            hints = (
                lint_report.consolidation_hints
                if lint_report is not None
                else None
            )
            dream_targets = pick_dream_targets(items, consolidation_hints=hints)

            engine = DreamEngine(
                strategy=dream_strategy,
                embedder=self.client.embedder,
            )
            report = engine.dream(items, targets=dream_targets)

            if report.total_dream_items == 0:
                return None

            # Write dream items to adapter
            from contextseek.domain.serialization import serialize_context_item

            all_dream_items = list(report.consolidation.items)
            if report.divergence:
                all_dream_items.extend(report.divergence.items)

            for item in all_dream_items:
                if self.client.embedder is not None:
                    item.embedding = self.client.embedder(item.content_text)
                payload = serialize_context_item(item)
                ref = self.client.resolver.ref_for(scope, item.id)
                self.client.adapter.write(ref, payload)

            event = LifecycleEvent(
                scope=scope,
                action="dream",
                timestamp=_utc_now(),
                result={
                    "consolidation_items": len(report.consolidation.items),
                    "divergence_items": len(report.divergence.items)
                    if report.divergence
                    else 0,
                    "patterns_found": report.consolidation.patterns_found,
                    "total": report.total_dream_items,
                    # Targets selected by pick_dream_targets() (graph-structure driven)
                    "targeted_consolidations": len(dream_targets.consolidation_pairs),
                    "targeted_divergences": len(dream_targets.divergence_candidates),
                    "lint_guided": lint_report is not None,
                },
            )
            self._history.append(event)
            if self.on_event:
                self.on_event(event)
            return event
        except Exception as exc:  # noqa: BLE001
            event = LifecycleEvent(
                scope=scope,
                action="dream_error",
                timestamp=_utc_now(),
                result={"error": str(exc)},
            )
            self._history.append(event)
            if self.on_event:
                self.on_event(event)
            return event

    def _export_scope(self, scope: str) -> LifecycleEvent | None:
        """Materialize prompt skills in *scope* to SKILL.md files on disk."""
        try:
            from contextseek.daemon.skill_export import export_skills

            report = export_skills(
                self.client,
                scope=scope,
                out_dir=self.export_dir,
                min_confidence=self.export_min_confidence,
            )
            event = LifecycleEvent(
                scope=scope,
                action="skill_export",
                timestamp=_utc_now(),
                result={
                    "written": report.written,
                    "unchanged": report.unchanged,
                    "pruned": report.pruned,
                    "skipped_low_confidence": report.skipped_low_confidence,
                },
            )
            self._history.append(event)
            if self.on_event:
                self.on_event(event)
            return event
        except Exception as exc:  # noqa: BLE001 — export must never block evolution
            event = LifecycleEvent(
                scope=scope,
                action="skill_export_error",
                timestamp=_utc_now(),
                result={"error": str(exc)},
            )
            self._history.append(event)
            if self.on_event:
                self.on_event(event)
            return event


__all__ = ["LifecycleScheduler", "LifecycleEvent"]
