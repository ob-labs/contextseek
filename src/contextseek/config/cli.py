"""`contextseek config` subcommand wiring."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from contextseek.config.manager import ConfigManager
from contextseek.config.materializer import Materializer


def _default_config_dir() -> Path:
    """Resolve ``${CONTEXTSEEK_HOME:-.contextseek}/config``."""
    home = os.environ.get("CONTEXTSEEK_HOME")
    root = Path(home) if home else Path.cwd() / ".contextseek"
    return root / "config"


def _default_materializer() -> Materializer:
    """Build the default materializer (``.env`` + ``config.json`` relative to CWD)."""
    env_path = Path(os.environ.get("CONTEXTSEEK_ENV_FILE", ".env"))
    runtime_path = Path(os.environ.get("CONTEXTSEEK_CONFIG", "config.json"))
    return Materializer(env_path=env_path, runtime_path=runtime_path)


def _manager() -> ConfigManager:
    m = ConfigManager(_default_config_dir())
    m.init_store()
    return m


def register_config_subparser(subparsers: Any) -> None:
    """Register the ``config`` subcommand group on ``subparsers``."""
    parser = subparsers.add_parser("config", help="manage contextseek configuration")
    sub = parser.add_subparsers(dest="config_command", required=True)

    p_show = sub.add_parser("show", help="show a config version/layer")
    p_show.add_argument("--version", default=None)
    p_show.add_argument(
        "--layer", choices=["native", "projected", "effective"], default="effective"
    )

    p_set = sub.add_parser("set", help="set a native config key")
    p_set.add_argument("key")
    p_set.add_argument("value")
    p_set.add_argument("--reason", default="cli set")
    p_set.add_argument("--author", default="cli")
    p_set.add_argument("--no-apply", action="store_true")

    sub.add_parser(
        "apply", help="materialize current config to .env + config.json"
    )

    p_hist = sub.add_parser("history", help="list version history")
    p_hist.add_argument("-n", type=int, default=None)

    p_diff = sub.add_parser("diff", help="diff two versions")
    p_diff.add_argument("a")
    p_diff.add_argument("b")

    p_rb = sub.add_parser("rollback", help="rollback to a version (append-only)")
    p_rb.add_argument("version")
    p_rb.add_argument("--reason", default="rollback")
    p_rb.add_argument("--author", default="cli")
    p_rb.add_argument("--no-apply", action="store_true")

    p_redo = sub.add_parser("redo", help="undo the most recent rollback")
    p_redo.add_argument("--reason", default="redo")
    p_redo.add_argument("--author", default="cli")
    p_redo.add_argument("--no-apply", action="store_true")

    p_blame = sub.add_parser("blame", help="find the version that last set a key")
    p_blame.add_argument("key")

    sub.add_parser("status", help="show current version / drift / source staleness")
    sub.add_parser("verify", help="verify history integrity (hash + parent chain)")

    p_ingest = sub.add_parser("ingest", help="ingest an external config source")
    p_ingest_sub = p_ingest.add_subparsers(dest="ingest_source", required=True)
    p_ingest_agent = p_ingest_sub.add_parser(
        "agentseek", help="ingest agentseek config"
    )
    p_ingest_agent.add_argument("--path", default=None)
    p_ingest_agent.add_argument("--apply", action="store_true")
    p_ingest_agent.add_argument("--author", default="agentseek")

    p_import = sub.add_parser("import", help="import existing .env / config.json as v1")
    p_import.add_argument("--from-env", default=None, help="path to .env (default: resolved .env)")
    p_import.add_argument("--from-runtime", default=None, help="path to config.json (default: CONTEXTSEEK_CONFIG)")
    p_import.add_argument("--apply", action="store_true")
    p_import.add_argument("--author", default="system")


def run_config_command(args: argparse.Namespace) -> int:
    """Dispatch a ``config`` subcommand. Returns process exit code."""
    cmd = args.config_command
    mgr = _manager()

    if cmd == "show":
        v = mgr.get_version(args.version) if args.version else mgr.current()
        if v is None:
            print("no config versions yet")
            return 0
        layer = v.payload.get(args.layer, {})
        print(json.dumps(layer, ensure_ascii=False, indent=2))
        return 0

    if cmd == "set":
        v = mgr.set_native(
            args.key, args.value, author=args.author, reason=args.reason
        )
        print(f"committed {v.version_id}")
        if not args.no_apply:
            mgr.apply(_default_materializer())
            print("applied to .env + config.json")
        return 0

    if cmd == "apply":
        mgr.apply(_default_materializer())
        print("applied current config to .env + config.json")
        return 0

    if cmd == "history":
        for v in mgr.history(n=args.n):
            print(
                f"{v.version_id}  {v.created_at}  {v.origin}  {v.author}  {v.reason}"
            )
        return 0

    if cmd == "diff":
        d = mgr.diff(args.a, args.b)
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0

    if cmd == "rollback":
        v = mgr.rollback(args.version, author=args.author, reason=args.reason)
        print(f"rolled back to {args.version} as {v.version_id}")
        if not args.no_apply:
            mgr.apply(_default_materializer())
            print("applied to .env + config.json")
        return 0

    if cmd == "redo":
        v = mgr.redo(author=args.author, reason=args.reason)
        if v is None:
            print("nothing to redo (latest version is not a rollback)")
            return 1
        print(f"redone as {v.version_id}")
        if not args.no_apply:
            mgr.apply(_default_materializer())
            print("applied to .env + config.json")
        return 0

    if cmd == "blame":
        info = mgr.blame(args.key)
        if info is None:
            print(f"no history for {args.key}")
            return 1
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    if cmd == "status":
        st = mgr.status()
        st["verify_problems"] = mgr.verify()
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0

    if cmd == "verify":
        problems = mgr.verify()
        if problems:
            for p in problems:
                print(f"PROBLEM: {p}")
            return 1
        print("OK")
        return 0

    if cmd == "ingest":
        from contextseek.config.agentseek_ingestor import AgentseekIngestor

        ing = AgentseekIngestor(mgr)
        if args.ingest_source == "agentseek":
            if args.path:
                v = ing.ingest_file(Path(args.path), author=args.author)
            else:
                v = ing.ingest_env(dict(os.environ), author=args.author)
            if v is None:
                print(
                    "no new agentseek config to ingest "
                    "(idempotent skip or empty)"
                )
                return 0
            print(
                f"ingested as {v.version_id} (source_ref={v.source_ref})"
            )
            if args.apply:
                mgr.apply(_default_materializer())
                print("applied to .env + config.json")
            return 0

    if cmd == "import":
        from contextseek.config.migrator import migrate_into

        env_path = Path(args.from_env) if args.from_env else None
        rt_path = Path(args.from_runtime) if args.from_runtime else None
        v = migrate_into(mgr, env_path=env_path, runtime_path=rt_path, author=args.author)
        if v is None:
            print("store already initialized; nothing to import")
            return 0
        print(f"imported as {v.version_id} (origin=migration)")
        if args.apply:
            mgr.apply(_default_materializer())
            print("applied to .env + config.json")
        return 0

    return 1
