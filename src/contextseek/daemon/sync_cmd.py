"""Format-agnostic sync: import notes, documents, and chat exports into ContextSeek.

Auto-detects the source format from path structure and file content.
No --from=<tool> flag required.
"""

from __future__ import annotations

import ast
import hashlib
import json
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

    Returns one of: obsidian, mixed_dir, markdown_dir, code_dir,
    markdown_file, code_file, chatgpt_json, claude_json, bookmarks_html,
    plaintext.
    """
    p = pathlib.Path(path).expanduser()

    if p.is_dir():
        if (p / ".obsidian").exists():
            return "obsidian"
        has_docs = bool(list(p.rglob("*.md"))[:1] or list(p.rglob("*.txt"))[:1])
        has_code = _has_code_files(p)
        if has_docs and has_code:
            return "mixed_dir"
        if has_docs:
            return "markdown_dir"
        if has_code:
            return "code_dir"
        return "plaintext"

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
    for fp in root.rglob("*"):
        if fp.is_file() and fp.suffix.lower() in _CODE_EXTENSIONS:
            return True
    return False


# ---------------------------------------------------------------------------
# Dedup helpers
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


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
    for fp in sorted(root.rglob("*.md")) + sorted(root.rglob("*.txt")):
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


def _parse_dir(root: pathlib.Path, *, include_docs: bool, include_code: bool) -> list[tuple[str, str]]:
    """Scan a directory for all supported file types."""
    results: list[tuple[str, str]] = []
    for fp in sorted(root.rglob("*")):
        if not fp.is_file():
            continue
        ext = fp.suffix.lower()
        try:
            if include_docs and ext in (".md", ".txt"):
                for para in _parse_markdown_file(fp):
                    results.append((fp.as_posix(), para))
            elif include_code and ext in _CODE_EXTENSIONS:
                for chunk in _parse_code_file(fp):
                    results.append((fp.as_posix(), chunk))
        except OSError:
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
    else:
        if on_progress is not None:
            on_progress(0, 0, 0)
        existing_hashes = set() if dry_run else _existing_hashes(ctx, scope)

    # Build (source_id, text) pairs from the appropriate parser
    pairs: list[tuple[str, str]] = []

    if fmt == "markdown_file":
        for para in _parse_markdown_file(p):
            pairs.append((p.as_posix(), para))

    elif fmt == "code_file":
        for chunk in _parse_code_file(p):
            pairs.append((p.as_posix(), chunk))

    elif fmt == "markdown_dir":
        pairs = _parse_dir(p, include_docs=True, include_code=False)

    elif fmt == "code_dir":
        pairs = _parse_dir(p, include_docs=False, include_code=True)

    elif fmt == "mixed_dir":
        pairs = _parse_dir(p, include_docs=True, include_code=True)

    elif fmt == "chatgpt_json":
        try:
            pairs = _parse_chatgpt_json(p)
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            report.errors.append(str(exc))

    elif fmt == "claude_json":
        try:
            pairs = _parse_claude_json(p)
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            report.errors.append(str(exc))

    elif fmt == "obsidian":
        # [[wikilinks]] are normalised to plain text by _parse_markdown_file;
        # full link-graph construction (wikilink → ContextItem.links) is deferred.
        pairs = _parse_dir(p, include_docs=True, include_code=False)
        report.format_detected = "obsidian"

    elif fmt == "bookmarks_html":
        try:
            pairs = _parse_bookmarks_html(p)
        except (OSError, Exception) as exc:
            report.errors.append(str(exc))

    else:
        # plaintext fallback: split by paragraph
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            for para in re.split(r"\n{2,}", text):
                para = para.strip()
                if len(para) > 20:
                    pairs.append((p.as_posix(), para))
        except OSError as exc:
            report.errors.append(str(exc))

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
                ctx.add(text, scope=scope, source=source_id, source_type="document")
                if seekdb_backend is not None:
                    seekdb_backend.sync_hash_add(scope, h)
                existing_hashes.add(h)
                report.added += 1
            except ValueError:
                report.skipped += 1
        if on_progress is not None:
            on_progress(report.added, report.skipped, total)

    return report
