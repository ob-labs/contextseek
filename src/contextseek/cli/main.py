"""Local CLI for ContextSeek business-level operations."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from contextseek.client.contextseek import ContextSeek
from contextseek.domain.serialization import (
    deserialize_context_item,
    serialize_context_item,
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
        help="retrieve ranked SearchHits (L1 summaries by default; --full for L2)",
    )
    retrieve_parser.add_argument("--scope", default=None)
    retrieve_parser.add_argument("--query", required=True)
    retrieve_parser.add_argument("--k", type=int, default=10)
    retrieve_parser.add_argument(
        "--full",
        action="store_true",
        help="return L2 full content instead of L1 summaries",
    )

    expand_parser = subparsers.add_parser(
        "expand",
        help="expand previously-retrieved item ids to L2 full content",
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

    return parser


def run_cli(
    argv: Sequence[str] | None = None, *, client: ContextSeek | None = None
) -> int:
    """Execute CLI command and return process exit code."""
    from contextseek.config.settings import ContextSeekSettings

    parser = build_parser()
    args = parser.parse_args(argv)

    settings = ContextSeekSettings()
    ctx = client or ContextSeek.from_settings(settings=settings)

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
        print(json.dumps(output, ensure_ascii=False))
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
        report = ctx.overview(scope=args.scope)
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
            from contextseek.domain.stages import Stage
            import pathlib

            skills = ctx.skills(args.scope)

            # Items approaching distillation threshold (access_count >= 3 but < 5)
            all_items = ctx.items(scope=args.scope)
            growing = [
                it
                for it in all_items
                if it.stage != Stage.skill
                and not it.is_deleted
                and it.access_count >= 3
            ]
            growing.sort(key=lambda x: x.access_count, reverse=True)

            # Last evolution time from lifecycle log
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

            print(
                render_overview(
                    scope=args.scope,
                    skills=skills,
                    report=report,
                    last_evolution=last_evolution,
                    growing_items=growing,
                    backend_label=storage_backend,
                )
            )
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

    if args.command == "init":
        from contextseek.daemon.init_cmd import run_init
        import pathlib

        run_init(pathlib.Path.home() / ".contextseek")
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

        if args.daemon_command == "stop":
            ok = daemon.stop()
            print("stopped" if ok else "daemon not running")
            return 0 if ok else 1

        if args.daemon_command == "status":
            info = daemon.status()
            if info["running"]:
                print(f"  contextseek daemon  ·  running  (PID {info['pid']})")
                if info.get("uptime"):
                    print(f"  uptime: {info['uptime']}")
                for k, v in info.get("components", {}).items():
                    mark = "✓" if v else "✗"
                    print(f"    {k:<24}  {mark}")
            else:
                print("  contextseek daemon  ·  not running")

            # Evolution stats from lifecycle log (last 7 days)
            import datetime as _dt
            from contextseek.daemon.logger import read_lifecycle_log
            _log_path = config_dir / "logs" / "lifecycle.jsonl"
            _entries = read_lifecycle_log(_log_path)
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
                    print(f"\n  演化统计（最近 7 天）")
                    print(f"    已演化 item: {_evolved}  ·  合并去重: {_merged}")
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
        import sys as _sys

        _p = _pl.Path(args.path).expanduser()
        fmt = detect_format(_p)
        print(f"  format : {fmt}")
        print(f"  scope  : {args.scope}")
        if args.dry_run:
            print("  mode   : dry-run")
        print("  scanning ...", flush=True)

        def _progress(added: int, skipped: int, total: int) -> None:
            if total == 0:
                _sys.stdout.write("\r  loading existing hashes ...              ")
                _sys.stdout.flush()
                return
            done = added + skipped
            pct = int(done * 100 / total) if total else 100
            _sys.stdout.write(
                f"\r  [{pct:3d}%] {done}/{total}  added={added}  skipped={skipped}  "
            )
            _sys.stdout.flush()

        report = sync_path(
            ctx, args.path, scope=args.scope,
            dry_run=args.dry_run, on_progress=_progress,
        )

        # Clear the progress line
        _sys.stdout.write("\r" + " " * 60 + "\r")
        _sys.stdout.flush()

        if report.errors:
            for err in report.errors:
                print(f"  error  : {err}")

        if args.dry_run:
            print(f"  [dry-run] would import {report.added} items ({report.skipped} already exist)")
        else:
            print(f"  done   : added {report.added}  skipped {report.skipped}")
        return 0

    return 1


def main() -> int:
    """Entry point used by `python -m contextseek.cli.main`."""
    return run_cli()
