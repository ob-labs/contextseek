#!/usr/bin/env python3
"""Propagate the canonical version from pyproject.toml to the desktop + dashboard
package manifests, so CLI/SDK, dashboard, and the desktop installer all report
the same version. pyproject.toml is the single source of truth (see `make bump`).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def read_pyproject_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text()
    m = re.search(r'(?m)^version = "([^"]+)"', text)
    if not m:
        sys.exit("error: could not find version in pyproject.toml")
    return m.group(1)


def replace_first(path: Path, pattern: str, replacement: str) -> bool:
    """Replace the first match of `pattern` in `path`. Returns True if changed."""
    if not path.exists():
        print(f"skip (missing): {path.relative_to(REPO_ROOT)}")
        return False
    text = path.read_text()
    new_text, n = re.subn(pattern, replacement, text, count=1)
    if n != 1:
        sys.exit(f"error: version line not found in {path.relative_to(REPO_ROOT)}")
    if new_text != text:
        path.write_text(new_text)
        print(f"updated: {path.relative_to(REPO_ROOT)}")
        return True
    print(f"unchanged: {path.relative_to(REPO_ROOT)}")
    return False


def main() -> int:
    version = read_pyproject_version()
    print(f"syncing version -> {version}")

    # JSON manifests: the package's own top-level "version" is the first such key
    # (dependency entries are keyed by package name, not "version").
    json_version = re.compile(r'"version":\s*"[^"]+"')
    replace_first(
        REPO_ROOT / "dashboard" / "package.json",
        json_version.pattern,
        f'"version": "{version}"',
    )
    replace_first(
        REPO_ROOT / "desktop" / "tauri" / "package.json",
        json_version.pattern,
        f'"version": "{version}"',
    )
    replace_first(
        REPO_ROOT / "desktop" / "tauri" / "src-tauri" / "tauri.conf.json",
        json_version.pattern,
        f'"version": "{version}"',
    )

    # Cargo.toml: the [package] version is the first line-anchored `version = `.
    replace_first(
        REPO_ROOT / "desktop" / "tauri" / "src-tauri" / "Cargo.toml",
        r'(?m)^version = "[^"]+"',
        f'version = "{version}"',
    )

    # Cargo.lock (untracked, but keep local builds consistent): the entry that
    # immediately follows `name = "contextseek-desktop"`.
    replace_first(
        REPO_ROOT / "desktop" / "tauri" / "src-tauri" / "Cargo.lock",
        r'(name = "contextseek-desktop"\nversion = ")[^"]+(")',
        rf'\g<1>{version}\g<2>',
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
