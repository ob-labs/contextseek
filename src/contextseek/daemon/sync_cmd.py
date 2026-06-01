"""Format-agnostic sync: import notes, documents, and chat exports into ContextSeek.

Auto-detects the source format from path structure and file content.
No --from=<tool> flag required.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import pathlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from contextseek.client.contextseek import ContextSeek

# Extensions treated as plain-text code (block-split, no AST)
_CODE_EXTENSIONS: set[str] = {
    ".py", ".pyi",
    ".js", ".jsx", ".ts", ".tsx",
    ".go", ".java", ".kt", ".scala",
    ".c", ".cpp", ".cc", ".h", ".hpp",
    ".rs", ".swift",
    ".sh", ".bash", ".zsh",
    ".yaml", ".yml", ".toml", ".ini", ".env",
    ".rst", ".tex",
    ".sql",
}

_IGNORED_DIR_NAMES: set[str] = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "site-packages",
}


def _iter_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Return files under root, skipping dependency/cache/build directories."""
    files: list[pathlib.Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _IGNORED_DIR_NAMES and not name.endswith(".egg-info")
        ]
        base = pathlib.Path(dirpath)
        for filename in filenames:
            files.append(base / filename)
    return files


@dataclass
class SyncReport:
    added: int = 0
    skipped: int = 0
    format_detected: str = "unknown"
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def detect_format(path: str | pathlib.Path) -> str:
    """Detect the source format from path structure and file content.

    Returns one of: auto_dir, markdown_file, code_file, chatgpt_json,
    claude_json, bookmarks_html, plaintext.
    """
    p = pathlib.Path(path).expanduser()

    if p.is_dir():
        return "auto_dir"

    if p.is_file():
        name = p.name.lower()
        if name == "bookmarks.html":
            return "bookmarks_html"
        if p.suffix.lower() in (".md", ".txt"):
            return "markdown_file"
        if p.suffix.lower() in _CODE_EXTENSIONS:
            return "code_file"
        if p.suffix.lower() == ".json":
            try:
                data = json.loads(p.read_bytes()[:4096])
                if isinstance(data, dict):
                    if "mapping" in data:
                        return "chatgpt_json"
                    if "conversations" in data:
                        return "claude_json"
            except (json.JSONDecodeError, OSError):
                pass
            return "plaintext"

    return "plaintext"


def _has_code_files(root: pathlib.Path) -> bool:
    """Return True if any code file exists under root (fast early-exit scan)."""
    for fp in _iter_files(root):
        if fp.is_file() and fp.suffix.lower() in _CODE_EXTENSIONS:
            return True
    return False


# ---------------------------------------------------------------------------
# Dedup helpers
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _file_hash(p: pathlib.Path) -> str:
    """Streamed SHA256 of a file's bytes (content-addressed dedup key)."""
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class _FileGate:
    """mtime-first / SHA256-authoritative skip filter for incremental sync.

    mtime is only a fast pre-filter: an unchanged mtime means skip without
    hashing. A changed mtime triggers a content hash; when the hash matches
    the stored record the file is still skipped (copy / ``git checkout`` /
    filesystem migration change mtime but not content) and the new mtime is
    recorded so the next run short-circuits again.
    """

    def __init__(self, records: dict[str, tuple[float, str]]):
        self._records = records
        self.updates: dict[str, tuple[float, str]] = {}
        self.skipped_files = 0

    def should_process(self, p: pathlib.Path) -> bool:
        key = p.as_posix()
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return True
        rec = self._records.get(key)
        if rec is not None and abs(rec[0] - mtime) < 1e-6:
            self.skipped_files += 1
            return False
        file_hash = _file_hash(p)
        if rec is not None and rec[1] == file_hash:
            # Content unchanged despite a new mtime — refresh mtime, skip parse.
            self.updates[key] = (mtime, file_hash)
            self.skipped_files += 1
            return False
        self.updates[key] = (mtime, file_hash)
        return True


def _existing_hashes(ctx: "ContextSeek", scope: str) -> set[str]:
    """Fallback: full scan of scope items to collect content hashes (O(N) reads)."""
    items = ctx.items(scope=scope)
    return {item.hash for item in items if item.hash}


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _normalize_wikilinks(text: str) -> str:
    """Convert Obsidian [[wikilinks]] to plain text for indexing.

    [[Page|Alias]] → Alias
    [[Page]]       → Page
    """
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    return text


def _parse_markdown_file(p: pathlib.Path) -> list[str]:
    """Split a Markdown file into paragraphs."""
    text = p.read_text(encoding="utf-8", errors="replace")
    # Strip YAML front-matter
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    text = _normalize_wikilinks(text)
    paragraphs = [blk.strip() for blk in re.split(r"\n{2,}", text)]
    return [p for p in paragraphs if len(p) > 20]


def _parse_markdown_dir(root: pathlib.Path) -> list[tuple[str, str]]:
    """Yield (source_id, text) pairs from all markdown/txt files in a directory."""
    results: list[tuple[str, str]] = []
    for fp in sorted(
        f for f in _iter_files(root) if f.suffix.lower() in (".md", ".txt")
    ):
        try:
            for para in _parse_markdown_file(fp):
                results.append((fp.as_posix(), para))
        except OSError:
            continue
    return results


def _parse_python_file(p: pathlib.Path) -> list[str]:
    """Extract semantic chunks from a Python file using AST.

    Produces one chunk per module/function/class with its signature,
    docstring, and a brief body preview — suitable for code retrieval.
    """
    try:
        source = p.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return _parse_generic_code_file(p)

    lines = source.splitlines()
    chunks: list[str] = []

    # Module docstring
    mod_doc = ast.get_docstring(tree)
    if mod_doc and len(mod_doc) > 20:
        chunks.append(f"[{p.name}]\n{mod_doc}")

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        try:
            sig_line = lines[node.lineno - 1].strip()
        except IndexError:
            sig_line = ""
        docstring = ast.get_docstring(node) or ""

        if isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in ast.walk(node)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            body = f"  methods: {', '.join(methods[:10])}" if methods else ""
            text = f"[{p.name} :: {node.name}]\n{sig_line}\n{docstring}\n{body}".strip()
        else:
            # Function: include first few body lines after docstring
            body_start = node.body[0].end_lineno + 1 if docstring else node.body[0].lineno
            body_lines = lines[body_start - 1 : body_start + 4]
            body_preview = "\n".join(l for l in body_lines if l.strip())
            text = f"[{p.name} :: {node.name}]\n{sig_line}\n{docstring}\n{body_preview}".strip()

        if len(text) > 30:
            chunks.append(text)

    return chunks


def _parse_generic_code_file(p: pathlib.Path) -> list[str]:
    """Split a code file into non-trivial blocks separated by blank lines."""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    chunks: list[str] = []
    for block in re.split(r"\n{2,}", text):
        block = block.strip()
        # Skip short lines or pure-comment blocks with no substance
        if len(block) > 40 and not all(
            l.lstrip().startswith(("#", "//", "*", "--")) for l in block.splitlines() if l.strip()
        ):
            chunks.append(f"[{p.name}]\n{block}")
    return chunks


def _parse_code_file(p: pathlib.Path) -> list[str]:
    """Dispatch to the right code parser based on extension."""
    if p.suffix.lower() in (".py", ".pyi"):
        return _parse_python_file(p)
    return _parse_generic_code_file(p)


def _parse_plaintext_file(p: pathlib.Path) -> list[str]:
    """Split a generic text file into paragraph-like chunks."""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    chunks: list[str] = []
    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if len(para) > 20:
            chunks.append(para)
    return chunks


def _is_probably_text_file(p: pathlib.Path, *, sample_size: int = 4096) -> bool:
    """Cheap binary guard for auto directory scans."""
    try:
        sample = p.read_bytes()[:sample_size]
    except OSError:
        return False
    if not sample:
        return False
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _parse_file_auto(p: pathlib.Path) -> list[tuple[str, str]]:
    """Parse one file using the best available parser for that file."""
    ext = p.suffix.lower()
    name = p.name.lower()

    if name == "bookmarks.html":
        return _parse_bookmarks_html(p)

    if ext in (".md", ".txt"):
        return [(p.as_posix(), para) for para in _parse_markdown_file(p)]

    if ext in _CODE_EXTENSIONS:
        return [(p.as_posix(), chunk) for chunk in _parse_code_file(p)]

    if ext == ".json":
        try:
            data = json.loads(p.read_bytes())
        except (json.JSONDecodeError, OSError):
            data = None
        if isinstance(data, dict) and "mapping" in data:
            return _parse_chatgpt_json(p)
        if (
            isinstance(data, list)
            or (isinstance(data, dict) and "conversations" in data)
        ):
            return _parse_claude_json(p)

    if _is_probably_text_file(p):
        return [(p.as_posix(), para) for para in _parse_plaintext_file(p)]

    return []


def _parse_dir_auto(
    root: pathlib.Path,
    *,
    should_process: "Callable[[pathlib.Path], bool] | None" = None,
) -> list[tuple[str, str]]:
    """Scan a directory and dispatch each file to its own parser."""
    results: list[tuple[str, str]] = []
    for fp in sorted(_iter_files(root)):
        if not fp.is_file():
            continue
        if should_process is not None and not should_process(fp):
            continue
        try:
            results.extend(_parse_file_auto(fp))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    return results


def _parse_bookmarks_html(p: pathlib.Path) -> list[tuple[str, str]]:
    """Extract bookmarks from a Netscape Bookmark Format HTML file.

    Parses <A HREF="url">title</A> entries and returns (source_id, text) pairs
    where text is "title — url".
    """
    from html.parser import HTMLParser

    class _BookmarkParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.results: list[tuple[str, str]] = []
            self._cur_href: str | None = None

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag.lower() == "a":
                self._cur_href = dict(attrs).get("href", "") or ""

        def handle_endtag(self, tag: str) -> None:
            if tag.lower() == "a":
                self._cur_href = None

        def handle_data(self, data: str) -> None:
            if self._cur_href and data.strip():
                title = data.strip()
                url = self._cur_href
                self.results.append((f"bookmarks://{url}", f"{title} — {url}"))
                self._cur_href = None

    parser = _BookmarkParser()
    try:
        parser.feed(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    return [(src, txt) for src, txt in parser.results if len(txt) > 10]


def _parse_chatgpt_json(p: pathlib.Path) -> list[tuple[str, str]]:
    """Extract assistant messages from a ChatGPT conversation export."""
    data = json.loads(p.read_bytes())
    results: list[tuple[str, str]] = []
    mapping = data.get("mapping", {})
    for node_id, node in mapping.items():
        msg = node.get("message")
        if not msg:
            continue
        role = (msg.get("author") or {}).get("role", "")
        if role != "assistant":
            continue
        parts = (msg.get("content") or {}).get("parts", [])
        text = " ".join(str(p) for p in parts if isinstance(p, str)).strip()
        if len(text) > 30:
            results.append((f"chatgpt://{node_id}", text))
    return results


def _parse_claude_json(p: pathlib.Path) -> list[tuple[str, str]]:
    """Extract assistant messages from a Claude conversation export."""
    data = json.loads(p.read_bytes())
    results: list[tuple[str, str]] = []
    conversations = data if isinstance(data, list) else data.get("conversations", [])
    for conv in conversations:
        conv_id = conv.get("uuid", conv.get("id", "unknown"))
        for msg in conv.get("chat_messages", conv.get("messages", [])):
            role = msg.get("sender", msg.get("role", ""))
            if role not in ("assistant", "ai"):
                continue
            text = ""
            content = msg.get("content", msg.get("text", ""))
            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                text = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                ).strip()
            if len(text) > 30:
                results.append((f"claude://{conv_id}", text))
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _resolve_seekdb_backend(ctx: "ContextSeek") -> "Any | None":
    """Return the SeekDBBackend if the active adapter uses one, else None."""
    try:
        from contextseek.storage.seekdb_backend import SeekDBBackend
        router = ctx.adapter._vfs._router
        _, route = router.resolve("contextseek://")
        backend = route.get("backend") if isinstance(route, dict) else None
        if isinstance(backend, SeekDBBackend):
            return backend
    except Exception:
        pass
    return None


def sync_path(
    ctx: "ContextSeek",
    path: str | pathlib.Path,
    *,
    scope: str,
    dry_run: bool = False,
    on_progress: "Callable[[int, int, int], None] | None" = None,
) -> SyncReport:
    """Import items from path into scope, auto-detecting format.

    Skips items whose content hash already exists in the scope to prevent
    duplicate imports on repeated runs.

    Args:
        ctx: ContextSeek client.
        path: File or directory to import.
        scope: Destination scope.
        dry_run: When True, detect and count without writing.
        on_progress: Optional callback(added, skipped, total) called after each item.

    Returns:
        SyncReport with added/skipped counts and format_detected.
    """
    p = pathlib.Path(path).expanduser()
    fmt = detect_format(p)
    report = SyncReport(format_detected=fmt)

    seekdb_backend = _resolve_seekdb_backend(ctx)
    if seekdb_backend is not None:
        if on_progress is not None:
            on_progress(0, 0, 0)
        existing_hashes: set[str] = seekdb_backend.sync_hashes_for_scope(scope)
        existing_file_records = seekdb_backend.sync_files_for_scope(scope)
        if (
            not dry_run
            and seekdb_backend.visible_count_for_scope(scope) == 0
            and (existing_hashes or existing_file_records)
        ):
            # The collection has no rows visible under the current metadata
            # schema, but old sync bookkeeping says the files were imported.
            # Treat the bookkeeping as stale so a clean unified re-import works
            # without requiring users to manually delete the whole database.
            existing_hashes = set()
            existing_file_records = {}
        # mtime fast-path: skip unchanged files before parsing/hashing.
        gate: "_FileGate | None" = _FileGate(existing_file_records)
    else:
        if on_progress is not None:
            on_progress(0, 0, 0)
        existing_hashes = set() if dry_run else _existing_hashes(ctx, scope)
        gate = None

    def _should(fp: pathlib.Path) -> bool:
        return gate.should_process(fp) if gate is not None else True

    # Build (source_id, text) pairs from the appropriate parser
    pairs: list[tuple[str, str]] = []

    if fmt == "auto_dir":
        pairs = _parse_dir_auto(p, should_process=_should)

    elif fmt == "markdown_file":
        if _should(p):
            for para in _parse_markdown_file(p):
                pairs.append((p.as_posix(), para))

    elif fmt == "code_file":
        if _should(p):
            for chunk in _parse_code_file(p):
                pairs.append((p.as_posix(), chunk))

    elif fmt == "chatgpt_json":
        if _should(p):
            try:
                pairs = _parse_chatgpt_json(p)
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                report.errors.append(str(exc))

    elif fmt == "claude_json":
        if _should(p):
            try:
                pairs = _parse_claude_json(p)
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                report.errors.append(str(exc))

    elif fmt == "bookmarks_html":
        if _should(p):
            try:
                pairs = _parse_bookmarks_html(p)
            except (OSError, Exception) as exc:
                report.errors.append(str(exc))

    else:
        if _should(p):
            for para in _parse_plaintext_file(p):
                pairs.append((p.as_posix(), para))

    total = len(pairs)
    for source_id, text in pairs:
        h = _content_hash(text)
        if h in existing_hashes:
            report.skipped += 1
        elif dry_run:
            existing_hashes.add(h)
            report.added += 1
        else:
            try:
                # Sync already performs content-hash deduplication. Skipping
                # write-time conflict checks keeps bulk imports fast; use
                # `contextseek lint` afterward for merge/contradiction review.
                ctx.add(
                    text,
                    scope=scope,
                    source=source_id,
                    source_type="document",
                    check_conflicts=False,
                )
                if seekdb_backend is not None:
                    seekdb_backend.sync_hash_add(scope, h)
                existing_hashes.add(h)
                report.added += 1
            except ValueError:
                report.skipped += 1
        if on_progress is not None:
            on_progress(report.added, report.skipped, total)

    # Persist per-file ingest records so the next run can short-circuit via mtime.
    if gate is not None and not dry_run:
        for file_path, (mtime, file_hash) in gate.updates.items():
            try:
                seekdb_backend.sync_file_record(scope, file_path, mtime, file_hash)
            except Exception:
                pass

    return report
