"""Local CLI for ContextSeek business-level operations."""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections.abc import Sequence

from contextseek.client.contextseek import ContextSeek
from contextseek.domain.serialization import (
    deserialize_context_item,
    serialize_context_item,
)
from contextseek.cli.ui import (
    SyncProgress,
    console,
    overview_progress,
    print_panel,
    print_success,
    render_daemon_status,
    render_retrieve,
    suppress_backend_noise,
)


def _get_backend_label(ctx: "ContextSeek") -> str:
    """Return a human-readable label for the active storage backend."""
    try:
        router = ctx.adapter._vfs._router
        _, route = router.resolve("contextseek://")
        backend = route.get("backend") if isinstance(route, dict) else None
        bname = type(backend).__name__
        if "SeekDB" in bname:
            return "seekdb embedded"
        elif "OceanBase" in bname:
            return "oceanbase"
        elif "File" in bname:
            return "file"
        elif "InMemory" in bname:
            return "memory"
    except Exception:
        pass
    return "local"


def _resolve_scope(args: argparse.Namespace, default_scope: str) -> str:
    """Return the effective scope, falling back to *default_scope* if --scope was omitted."""
    scope = getattr(args, "scope", None) or default_scope
    if not scope:
        raise SystemExit(
            "error: --scope is required (or set DEFAULT_SCOPE in config.env)"
        )
    return scope


def _positive_int(value: str) -> int:
    """Validate and parse a positive integer (> 0)."""
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError(
            f"invalid positive int value: {value!r} (must be > 0)"
        )
    return n


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser for commands."""
    parser = argparse.ArgumentParser(prog="contextseek")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # add
    add_parser = subparsers.add_parser("add", help="add a context item")
    add_parser.add_argument("--scope", default=None)
    add_parser.add_argument("--content", required=True)
    add_parser.add_argument("--source", default="cli")
    add_parser.add_argument("--tags", default="")

    retrieve_parser = subparsers.add_parser(
        "retrieve",
        help="retrieve ranked SearchHits (L1 summaries by default; --full for L0)",
    )
    retrieve_parser.add_argument("--scope", default=None)
    retrieve_parser.add_argument("--query", required=True)
    retrieve_parser.add_argument("--k", type=_positive_int, default=10)
    retrieve_parser.add_argument(
        "--full",
        action="store_true",
        help="return L0 full content instead of L1 summaries",
    )
    retrieve_parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of human-readable output",
    )
    retrieve_parser.add_argument(
        "--verbose",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    expand_parser = subparsers.add_parser(
        "expand",
        help="expand previously-retrieved item ids to L0 full content",
    )
    expand_parser.add_argument("--scope", default=None)
    expand_parser.add_argument(
        "--ids",
        required=True,
        help="comma-separated list of item ids",
    )

    # compact
    compact_parser = subparsers.add_parser("compact", help="compact/evolve scope")
    compact_parser.add_argument("--scope", default=None)
    compact_parser.add_argument("--dry-run", action="store_true")

    # forget
    forget_parser = subparsers.add_parser("forget", help="soft-delete an item")
    forget_parser.add_argument("--scope", default=None)
    forget_parser.add_argument("--item-id", required=True)
    forget_parser.add_argument("--reason", default="cli_forget")

    delete_parser = subparsers.add_parser(
        "delete", help="permanently remove an item from storage (adapter delete)"
    )
    delete_parser.add_argument("--scope", default=None)
    delete_parser.add_argument("--item-id", required=True)
    delete_parser.add_argument("--reason", default="cli_delete")
    delete_parser.add_argument(
        "--no-propagate",
        action="store_true",
        help="skip invalidation propagation to dependent items",
    )

    # overview (stage distribution + evolution candidate counts)
    evo_parser = subparsers.add_parser(
        "overview", help="scope summary: skills, growth progress, and item counts"
    )
    evo_parser.add_argument("--scope", default=None)
    evo_parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of human-readable output",
    )

    # tools — print LLM tool spec for retrieve/expand
    tools_parser = subparsers.add_parser(
        "tools",
        help="print ContextSeek LLM tool spec (OpenAI/Anthropic format)",
    )
    tools_parser.add_argument(
        "--format",
        choices=["openai", "anthropic"],
        default="openai",
    )

    # metrics
    subparsers.add_parser("metrics", help="print prometheus metrics")

    # dream
    dream_parser = subparsers.add_parser(
        "dream", help="trigger dream cycle (consolidation + divergence)"
    )
    dream_parser.add_argument("--scope", default=None)
    dream_parser.add_argument("--dry-run", action="store_true")

    # lint
    lint_parser = subparsers.add_parser(
        "lint",
        help="health-check the knowledge base: orphans, contradictions, distillation gaps",
    )
    lint_parser.add_argument("--scope", default=None)
    lint_parser.add_argument(
        "--fix",
        action="store_true",
        help="auto-archive orphan items (low-risk automatic fix)",
    )
    lint_parser.add_argument(
        "--show",
        nargs=2,
        metavar=("ID1", "ID2"),
        default=None,
        help="show full content of two items side-by-side (for contradiction review)",
    )
    lint_parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of human-readable output",
    )

    # feedback
    feedback_parser = subparsers.add_parser(
        "feedback", help="apply relevance feedback to an item"
    )
    feedback_parser.add_argument("--scope", default=None)
    feedback_parser.add_argument("--item-id", required=True)
    feedback_parser.add_argument(
        "--score", type=float, required=True, help="feedback score delta (-1.0 to 1.0)"
    )
    feedback_parser.add_argument("--reason", default="")

    # upstream
    upstream_parser = subparsers.add_parser(
        "upstream", help="walk derived_from/supported_by links to find upstream items"
    )
    upstream_parser.add_argument("--scope", default=None)
    upstream_parser.add_argument("--item-id", required=True)

    # evidence-chain
    ec_parser = subparsers.add_parser(
        "evidence-chain", help="compute full evidence chain DAG for an item"
    )
    ec_parser.add_argument("--scope", default=None)
    ec_parser.add_argument("--item-id", required=True)
    ec_parser.add_argument("--max-depth", type=int, default=10)

    # chain-confidence
    cc_parser = subparsers.add_parser(
        "chain-confidence", help="quick propagated confidence lookup for an item"
    )
    cc_parser.add_argument("--scope", default=None)
    cc_parser.add_argument("--item-id", required=True)

    # skill-tools
    st_parser = subparsers.add_parser(
        "skill-tools", help="export tool/mcp skills as LLM tool definitions"
    )
    st_parser.add_argument("--scope", default=None)
    st_parser.add_argument(
        "--fmt", choices=["openai", "anthropic", "mcp"], default="openai"
    )
    st_parser.add_argument(
        "--query", default=None, help="optional semantic search query"
    )
    st_parser.add_argument("--k", type=int, default=20)

    # skill-context
    sc_parser = subparsers.add_parser(
        "skill-context", help="render prompt skills as a system prompt block"
    )
    sc_parser.add_argument("--scope", default=None)
    sc_parser.add_argument(
        "--query", default=None, help="optional semantic search query"
    )
    sc_parser.add_argument("--k", type=int, default=5)

    # skill-import
    si_parser = subparsers.add_parser(
        "skill-import", help="import skills from Hermes, OpenAI, or MCP format"
    )
    si_parser.add_argument("--scope", default=None)
    si_parser.add_argument(
        "--format", choices=["hermes", "openai", "mcp"], required=True
    )
    si_parser.add_argument(
        "--path",
        required=True,
        help="directory path (hermes) or JSON file path (openai/mcp)",
    )

    # skill-export
    se_parser = subparsers.add_parser(
        "skill-export",
        help="materialize prompt skills as SKILL.md files for agent tools",
    )
    se_parser.add_argument("--scope", default=None)
    se_parser.add_argument(
        "--out",
        default=None,
        help="output dir (default: SKILL_EXPORT_DIR or ~/.contextseek/skills)",
    )
    se_parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.8,
        help="exclude skills below this provenance confidence (default 0.8)",
    )
    se_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="count what would be written/pruned without touching disk",
    )
    se_parser.add_argument(
        "--no-prune",
        action="store_true",
        help="keep previously-exported skills that no longer exist",
    )

    # items
    items_parser = subparsers.add_parser("items", help="list all items in a scope")
    items_parser.add_argument("--scope", default=None)
    items_parser.add_argument(
        "--stage", default=None, help="filter by stage (raw/extracted/knowledge/skill)"
    )

    # init
    subparsers.add_parser(
        "init",
        help="initialize ~/.contextseek/: generate config.env, mcp.json, register system service",
    )

    # daemon
    daemon_parser = subparsers.add_parser("daemon", help="manage the background daemon")
    daemon_sub = daemon_parser.add_subparsers(dest="daemon_command", required=True)
    daemon_start = daemon_sub.add_parser("start", help="start the daemon")
    daemon_start.add_argument(
        "--config-dir",
        default=None,
        help="path to config directory (default: ~/.contextseek)",
    )
    daemon_start.add_argument(
        "--foreground",
        action="store_true",
        help="run in foreground (used by systemd/launchd; default is background)",
    )
    daemon_sub.add_parser("stop", help="stop a running daemon")
    daemon_sub.add_parser("status", help="show daemon status")
    daemon_sub.add_parser("restart", help="restart the daemon")

    # sync
    sync_parser = subparsers.add_parser(
        "sync", help="import notes/documents from a file or directory"
    )
    sync_parser.add_argument("path", help="file or directory to import")
    sync_parser.add_argument("--scope", default=None)
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="detect format and count items without writing",
    )

    # desktop-server — single-process backend for the desktop app: serves the
    # API and the built dashboard SPA from one origin.
    desktop_parser = subparsers.add_parser(
        "desktop-server",
        help="run the same-origin backend (API + dashboard SPA) for the desktop app",
    )
    desktop_parser.add_argument("--host", default="127.0.0.1")
    desktop_parser.add_argument("--port", type=int, default=None)
    desktop_parser.add_argument(
        "--data-dir",
        default=None,
        help="data directory (default: platform app-data dir)",
    )
    desktop_parser.add_argument("--log-level", default="info")

    return parser


def run_cli(
    argv: Sequence[str] | None = None, *, client: ContextSeek | None = None
) -> int:
    """Execute CLI command and return process exit code."""
    from contextseek.config.settings import ContextSeekSettings

    parser = build_parser()
    args = parser.parse_args(argv)

    # desktop-server sets storage/data-dir env defaults before settings are read,
    # so it must run before ContextSeekSettings() below.
    if args.command == "desktop-server":
        from contextseek.cli.desktop import run_desktop_server

        return run_desktop_server(args)

    settings = ContextSeekSettings()

    # Local scaffolding/process-state commands should not open the storage
    # backend. This keeps `contextseek init` usable before seekdb exists.
    if args.command == "init":
        from contextseek.daemon.init_cmd import run_init
        import pathlib

        run_init(pathlib.Path.home() / ".contextseek")
        return 0

    if args.command == "daemon" and args.daemon_command in {"stop", "status"}:
        from contextseek.daemon.process import DaemonProcess
        import pathlib

        config_dir = pathlib.Path(
            getattr(args, "config_dir", None) or pathlib.Path.home() / ".contextseek"
        )
        daemon = DaemonProcess(config_dir=config_dir)

        if args.daemon_command == "stop":
            ok = daemon.stop()
            print("stopped" if ok else "daemon not running")
            return 0 if ok else 1

        # Evolution stats from lifecycle log (last 7 days)
        import datetime as _dt
        from contextseek.daemon.logger import read_lifecycle_log

        _log_path = config_dir / "logs" / "lifecycle.jsonl"
        _entries = read_lifecycle_log(_log_path)
        _evolved = None
        _merged = None
        if _entries:
            _cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=7)
            _recent = []
            for _e in _entries:
                _ts = _e.get("ts")
                if _ts:
                    try:
                        if _dt.datetime.fromisoformat(_ts) > _cutoff:
                            _recent.append(_e)
                    except ValueError:
                        pass
            if _recent:
                _evolved = sum(_e.get("evolved_count", 0) for _e in _recent)
                _merged = sum(_e.get("merged_count", 0) for _e in _recent)
        render_daemon_status(daemon.status(), evolved=_evolved, merged=_merged)
        return 0

    if client is None:
        with suppress_backend_noise():
            ctx = ContextSeek.from_settings(settings=settings)
    else:
        ctx = client

    # Resolve --scope early: fill in default_scope when --scope is omitted.
    if hasattr(args, "scope"):
        args.scope = _resolve_scope(args, settings.default_scope)

    if args.command == "add":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        item = ctx.add(
            args.content,
            scope=args.scope,
            source=args.source,
            tags=tags,
        )
        print(
            json.dumps({"id": item.id, "stage": item.stage.value}, ensure_ascii=False)
        )
        return 0

    if args.command == "retrieve":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            response = ctx.retrieve(
                args.query,
                scope=args.scope,
                k=args.k,
                full=args.full,
            )
        output = {
            "items": [
                {
                    "id": h.item.id,
                    "score": h.score,
                    "layer": h.layer,
                    "stage": h.item.stage.value,
                    "source": h.item.provenance.source_id,
                    "abstract": h.item.abstract,
                    "summary": h.item.summary,
                    "content": h.item.content_text if h.layer == "full" else None,
                }
                for h in response
            ],
            "_meta": {
                "layer": response.meta.layer,
                "full_via": response.meta.full_via,
                "hint": response.meta.hint,
            },
        }
        if args.json:
            print(json.dumps(output, ensure_ascii=False))
        else:
            render_retrieve(args.scope, args.query, response)
        return 0

    if args.command == "expand":
        ids = [i.strip() for i in args.ids.split(",") if i.strip()]
        items: list = []
        for iid in ids:
            ref = ctx.resolver.ref_for(args.scope, iid)
            payload = ctx.adapter.read(ref)
            if payload is None:
                continue
            try:
                items.append(deserialize_context_item(payload))
            except (KeyError, TypeError, ValueError):
                continue
        print(
            json.dumps(
                {"items": [serialize_context_item(it) for it in items]},
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "compact":
        report = ctx.compact(scope=args.scope, dry_run=args.dry_run)
        print(
            json.dumps(
                {
                    "merged": report.merged_count,
                    "archived": report.archived_count,
                    "evolved": report.evolved_count,
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "forget":
        ref = (
            args.item_id
            if args.item_id.startswith(ctx.resolver.scheme)
            else ctx.resolver.ref_for(args.scope, args.item_id)
        )
        ctx.forget(ref, scope=args.scope, reason=args.reason)
        print(json.dumps({"status": "ok", "id": args.item_id}, ensure_ascii=False))
        return 0

    if args.command == "delete":
        ref = (
            args.item_id
            if args.item_id.startswith(ctx.resolver.scheme)
            else ctx.resolver.ref_for(args.scope, args.item_id)
        )
        ctx.delete(
            ref,
            scope=args.scope,
            reason=args.reason,
            propagate=not args.no_propagate,
        )
        print(json.dumps({"status": "ok", "id": args.item_id}, ensure_ascii=False))
        return 0

    if args.command == "overview":
        from contextseek.domain.stages import Stage

        with suppress_backend_noise(), overview_progress():
            all_items = ctx.items(scope=args.scope)
            report = ctx.overview_from_items(all_items, scope=args.scope)

        if args.json:
            print(
                json.dumps(
                    {
                        "total_items": report.total_items,
                        "stage_distribution": report.stage_distribution,
                        "pending_extraction": report.pending_extraction,
                        "pending_convergence": report.pending_convergence,
                        "distill_candidates": report.distill_candidates,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            from contextseek.cli.overview_renderer import render_overview
            from contextseek.daemon.logger import read_lifecycle_log
            import pathlib

            skills = [it for it in all_items if it.stage == Stage.skill]

            distill_threshold = 5
            growing = [
                it
                for it in all_items
                if it.stage != Stage.skill
                and not it.is_deleted
                and 3 <= it.access_count < distill_threshold
            ]
            growing.sort(key=lambda x: x.access_count, reverse=True)

            log_path = pathlib.Path.home() / ".contextseek" / "logs" / "lifecycle.jsonl"
            last_evolution = None
            if log_path.exists():
                entries = read_lifecycle_log(str(log_path))
                if entries:
                    import datetime as _dt

                    ts = entries[-1].get("ts")
                    if ts:
                        try:
                            last_evolution = _dt.datetime.fromisoformat(ts)
                        except ValueError:
                            pass

            storage_backend = _get_backend_label(ctx)

            lint_rpt = None
            try:
                from contextseek.evolution.lint import run_lint

                lint_rpt = run_lint(all_items, scope=args.scope, quick=True)
            except Exception:
                pass

            overview_text = render_overview(
                scope=args.scope,
                skills=skills,
                report=report,
                last_evolution=last_evolution,
                growing_items=growing,
                distill_threshold=distill_threshold,
                backend_label=storage_backend,
                all_items=all_items,
                lint_report=lint_rpt,
            )
            if console:
                console.print(overview_text)
            else:
                print(overview_text)
        return 0

    if args.command == "tools":
        specs = ctx.tools()
        if args.format == "openai":
            payload = [s.to_openai() for s in specs]
        else:
            payload = [s.to_anthropic() for s in specs]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "metrics":
        print(ctx.audit_log.export_prometheus() if ctx.audit_log is not None else "")
        return 0

    if args.command == "dream":
        report = ctx.dream(scope=args.scope, dry_run=args.dry_run)
        print(
            json.dumps(
                {
                    "total_dream_items": report.total_dream_items,
                    "consolidation_patterns": report.consolidation.patterns_found,
                    "consolidation_items": len(report.consolidation.items),
                    "divergence_items": len(report.divergence.items)
                    if report.divergence
                    else 0,
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "lint":
        from contextseek.evolution.lint import run_lint
        from contextseek.evolution.distiller import HeuristicDistillRule

        all_items = ctx.items(scope=args.scope)

        if args.show:
            # Side-by-side comparison for contradiction review
            id1, id2 = args.show
            matched = {
                it.id: it
                for it in all_items
                if it.id.startswith(id1) or it.id.startswith(id2)
            }
            for iid in (id1, id2):
                it = next((v for k, v in matched.items() if k.startswith(iid)), None)
                if it:
                    print(
                        f"\n  [{iid[:8]}] stage={it.stage.value}  access={it.access_count}"
                    )
                    print(f"  {it.content_text[:400]}")
                else:
                    print(f"\n  [{iid[:8]}] not found")
            return 0

        rule = HeuristicDistillRule()
        report = run_lint(all_items, scope=args.scope, heuristic_rule=rule)

        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False))
            return 0

        # Human-readable output
        from contextseek.cli.overview_renderer import _divider

        print()
        print(f"  ContextSeek · {args.scope}  knowledge-base health check")
        print()

        if report.contradictions:
            print(_divider("⚠ Potential contradictions"))
            for c in report.contradictions:
                print(f"    #{c.item_a_id[:8]}  {c.preview_a[:60]!r}")
                print(
                    f"      ← #{c.item_b_id[:8]}  {c.preview_b[:60]!r}  (sim={c.similarity})"
                )
                print(
                    f"      contextseek lint --scope {args.scope} --show {c.item_a_id[:8]} {c.item_b_id[:8]}"
                )
            print()

        if report.orphans:
            print(_divider("○ Orphan items"))
            for o in report.orphans[:10]:
                print(
                    f"    #{o.item_id[:8]}  stage={o.stage}  {o.content_preview[:60]!r}"
                )
            if len(report.orphans) > 10:
                print(f"    ... and {len(report.orphans) - 10} more")
            if args.fix:
                archived = 0
                for o in report.orphans:
                    ref = ctx.resolver.ref_for(args.scope, o.item_id)
                    try:
                        ctx.forget(
                            ref, scope=args.scope, reason="lint_auto_archive_orphan"
                        )
                        archived += 1
                    except Exception:
                        pass
                print(f"    --fix: archived {archived} orphan items")
            print()

        if report.distill_opportunities:
            print(_divider("✦ Distillation opportunities"))
            for d in report.distill_opportunities:
                print(
                    f"    #{d.item_id[:8]}  access={d.access_count}"
                    f"  stage={d.stage}  {d.content_preview[:50]!r}"
                )
            print()

        bar = "█" * (report.health_score // 10) + "░" * (10 - report.health_score // 10)
        print(f"  Health  {bar}  {report.health_score}/100")
        if not report.is_healthy():
            print(
                "  Run `contextseek compact` to distill items, or `contextseek dream` to consolidate knowledge."
            )
        print()
        return 0

    if args.command == "feedback":
        ref = (
            args.item_id
            if args.item_id.startswith(ctx.resolver.scheme)
            else ctx.resolver.ref_for(args.scope, args.item_id)
        )
        ctx.feedback(ref, scope=args.scope, score=args.score, reason=args.reason)
        print(json.dumps({"status": "ok", "id": args.item_id}, ensure_ascii=False))
        return 0

    if args.command == "upstream":
        ref = (
            args.item_id
            if args.item_id.startswith(ctx.resolver.scheme)
            else ctx.resolver.ref_for(args.scope, args.item_id)
        )
        chain = ctx.upstream(ref, scope=args.scope)
        print(
            json.dumps(
                {"items": [serialize_context_item(it) for it in chain]},
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "evidence-chain":
        ref = (
            args.item_id
            if args.item_id.startswith(ctx.resolver.scheme)
            else ctx.resolver.ref_for(args.scope, args.item_id)
        )
        chain = ctx.evidence_chain(ref, scope=args.scope, max_depth=args.max_depth)
        print(json.dumps(chain.to_dict(), ensure_ascii=False))
        return 0

    if args.command == "chain-confidence":
        ref = (
            args.item_id
            if args.item_id.startswith(ctx.resolver.scheme)
            else ctx.resolver.ref_for(args.scope, args.item_id)
        )
        confidence = ctx.chain_confidence(ref, scope=args.scope)
        print(json.dumps({"confidence": confidence}, ensure_ascii=False))
        return 0

    if args.command == "skill-tools":
        tools = ctx.skill_tools(
            args.scope, fmt=args.fmt, query=args.query or None, k=args.k
        )
        print(json.dumps({"tools": tools}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "skill-context":
        context = ctx.skill_context(args.scope, query=args.query or None, k=args.k)
        print(json.dumps({"context": context}, ensure_ascii=False))
        return 0

    if args.command == "skill-import":
        from contextseek.plugs.skills import (
            HermesSkillImporter,
            MCPToolImporter,
            OpenAIFunctionImporter,
        )

        if args.format == "hermes":
            plug = HermesSkillImporter(args.path)
        elif args.format == "openai":
            with open(args.path, encoding="utf-8") as f:
                functions = json.load(f)
            plug = OpenAIFunctionImporter(functions)
        else:  # mcp
            with open(args.path, encoding="utf-8") as f:
                mcp_data = json.load(f)
            tools_list = (
                mcp_data if isinstance(mcp_data, list) else mcp_data.get("tools", [])
            )
            plug = MCPToolImporter(tools_list)

        ctx.plug(plug, scope=args.scope)
        skills = ctx.skills(args.scope)
        print(
            json.dumps(
                {"imported": len(skills), "scope": args.scope}, ensure_ascii=False
            )
        )
        return 0

    if args.command == "skill-export":
        from contextseek.daemon.skill_export import export_skills
        import pathlib as _pl

        out_dir = (
            args.out
            or settings.skill_export_dir
            or str(_pl.Path.home() / ".contextseek" / "skills")
        )
        out_path = _pl.Path(out_dir).expanduser()
        subtitle = "dry-run" if args.dry_run else None
        print_panel(
            "ContextSeek skill-export",
            f"scope: {args.scope}\nout  : {out_path}\nmin-confidence: {args.min_confidence}",
            subtitle=subtitle,
        )
        report = export_skills(
            ctx,
            scope=args.scope,
            out_dir=out_path,
            min_confidence=args.min_confidence,
            dry_run=args.dry_run,
            prune=not args.no_prune,
        )
        verb = "would write" if args.dry_run else "wrote"
        print_success(
            f"{verb} {report.written}  unchanged {report.unchanged}  "
            f"pruned {report.pruned}  skipped(low-confidence) {report.skipped_low_confidence}"
        )
        return 0

    if args.command == "items":
        from contextseek.domain.stages import Stage

        stage = Stage(args.stage) if args.stage else None
        result_items = ctx.items(scope=args.scope, stage=stage)
        print(
            json.dumps(
                {"items": [serialize_context_item(it) for it in result_items]},
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "daemon":
        from contextseek.daemon.process import DaemonProcess
        import pathlib

        config_dir = pathlib.Path(
            getattr(args, "config_dir", None) or pathlib.Path.home() / ".contextseek"
        )
        daemon = DaemonProcess(config_dir=config_dir)

        if args.daemon_command == "start":
            if daemon.is_running():
                print(f"  daemon already running (PID {daemon._read_pid()})")
                return 0
            if getattr(args, "foreground", False):
                daemon.start_foreground(ctx)
                return 0
            # Background mode: spawn self with --foreground and detach
            import shutil
            import subprocess
            import time

            bin_path = shutil.which("contextseek") or sys.argv[0]
            cmd = [bin_path, "daemon", "start", "--foreground"]
            if getattr(args, "config_dir", None):
                cmd += ["--config-dir", args.config_dir]
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            pid_file = config_dir / "daemon.pid"
            for _ in range(50):
                if pid_file.exists() and daemon.is_running():
                    break
                time.sleep(0.1)
            if daemon.is_running():
                print(f"  contextseek daemon started (PID {daemon._read_pid()})")
            else:
                print("  daemon failed to start — check logs in ~/.contextseek/logs/")
                return 1
            return 0

        if args.daemon_command == "restart":
            daemon.stop()
            import time

            time.sleep(0.5)
            args.foreground = False
            args.daemon_command = "start"
            # re-enter start logic above via tail-call replacement
            import shutil
            import subprocess

            bin_path = shutil.which("contextseek") or sys.argv[0]
            cmd = [bin_path, "daemon", "start", "--foreground"]
            if getattr(args, "config_dir", None):
                cmd += ["--config-dir", args.config_dir]
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            pid_file = config_dir / "daemon.pid"
            for _ in range(50):
                if pid_file.exists() and daemon.is_running():
                    break
                time.sleep(0.1)
            if daemon.is_running():
                print(f"  contextseek daemon restarted (PID {daemon._read_pid()})")
            else:
                print("  daemon failed to restart — check logs in ~/.contextseek/logs/")
                return 1
            return 0

    if args.command == "sync":
        from contextseek.daemon.sync_cmd import detect_format, sync_path
        import pathlib as _pl

        _p = _pl.Path(args.path).expanduser()
        fmt = detect_format(_p)
        format_label = {
            "auto_dir": "auto-detected directory",
            "markdown_file": "Markdown/text file",
            "code_file": "code file",
            "chatgpt_json": "ChatGPT export",
            "claude_json": "Claude export",
            "bookmarks_html": "browser bookmarks",
            "plaintext": "plain text",
        }.get(fmt, fmt)
        subtitle = "dry-run" if args.dry_run else None
        print_panel(
            "ContextSeek sync",
            f"format: {format_label}\nscope : {args.scope}\npath  : {_p}",
            subtitle=subtitle,
        )

        with SyncProgress() as progress:
            report = sync_path(
                ctx,
                args.path,
                scope=args.scope,
                dry_run=args.dry_run,
                on_progress=progress.update,
            )

        if report.errors:
            for err in report.errors:
                print(f"  error  : {err}")

        if args.dry_run:
            print_success(
                f"dry-run: would import {report.added} items "
                f"({report.skipped} already exist)"
            )
        else:
            print_success(f"done: added {report.added}  skipped {report.skipped}")
        return 0

    return 1


def main() -> int:
    """Entry point used by `python -m contextseek.cli.main`."""
    return run_cli()
