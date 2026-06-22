"""Terminal UI helpers for the ContextSeek CLI.

The CLI should feel pleasant for humans while still degrading cleanly in plain
terminals, scripts, and test environments. Rich is the primary renderer; every
helper has a small text fallback.
"""

from __future__ import annotations

import contextlib
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

try:  # pragma: no cover - fallback exists for minimal environments
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskID,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table
    from rich.text import Text

    _RICH_AVAILABLE = True
except Exception:  # pragma: no cover
    Console = None  # type: ignore[assignment,misc]
    Group = None  # type: ignore[assignment,misc]
    Panel = None  # type: ignore[assignment,misc]
    Progress = None  # type: ignore[assignment,misc]
    Table = None  # type: ignore[assignment,misc]
    Text = None  # type: ignore[assignment,misc]
    BarColumn = None  # type: ignore[assignment,misc]
    SpinnerColumn = None  # type: ignore[assignment,misc]
    TaskID = int  # type: ignore[misc,assignment]
    TextColumn = None  # type: ignore[assignment,misc]
    TimeElapsedColumn = None  # type: ignore[assignment,misc]
    _RICH_AVAILABLE = False


console = Console() if _RICH_AVAILABLE else None


class _FilteredStream:
    """Drop known-noisy backend lines while preserving other stderr/stdout output."""

    _DROP_MARKERS = ("seekdb has opened", "[seekdb]")

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def write(self, data: str) -> int:
        if not data:
            return 0
        if any(marker in data for marker in self._DROP_MARKERS):
            return len(data)
        return self._stream.write(data)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


@contextmanager
def suppress_backend_noise() -> Iterator[None]:
    """Hide noisy backend startup lines (e.g. pyseekdb) on fd 1/2."""
    import os
    import sys
    import threading

    try:
        sys.stdout.flush()
        sys.stderr.flush()
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError):
        out_filter = _FilteredStream(sys.stdout)
        err_filter = _FilteredStream(sys.stderr)
        with (
            contextlib.redirect_stdout(out_filter),
            contextlib.redirect_stderr(err_filter),
        ):
            yield
        return

    drop_markers = tuple(
        marker.encode("utf-8") for marker in _FilteredStream._DROP_MARKERS
    )
    originals: list[int] = []
    pipes: list[tuple[int, int, int]] = []
    threads: list[threading.Thread] = []

    def _forward(read_fd: int, original_fd: int) -> None:
        try:
            while True:
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    break
                for part in chunk.splitlines(keepends=True):
                    if any(marker in part for marker in drop_markers):
                        continue
                    os.write(original_fd, part)
        finally:
            os.close(read_fd)

    try:
        for fd in (stdout_fd, stderr_fd):
            original_fd = os.dup(fd)
            read_fd, write_fd = os.pipe()
            os.dup2(write_fd, fd)
            os.close(write_fd)
            originals.append(original_fd)
            pipes.append((fd, read_fd, original_fd))
            thread = threading.Thread(
                target=_forward,
                args=(read_fd, original_fd),
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        yield
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        for fd, _read_fd, original_fd in pipes:
            os.dup2(original_fd, fd)
        for thread in threads:
            thread.join(timeout=0.5)
        for original_fd in originals:
            os.close(original_fd)


@contextmanager
def overview_progress() -> Iterator[None]:
    """Show a spinner while overview loads items from storage."""
    if not _RICH_AVAILABLE or Progress is None or SpinnerColumn is None:
        print("Loading scope items...", flush=True)
        try:
            yield
        finally:
            print()
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task("Loading scope overview...", total=None)
        yield


def print_success(message: str) -> None:
    if console:
        console.print(f"[green]✓[/green] {message}")
    else:
        print(message)


def print_error(message: str) -> None:
    if console:
        console.print(f"[red]error[/red] {message}")
    else:
        print(f"error: {message}")


def print_panel(title: str, body: str, *, subtitle: str | None = None) -> None:
    if console:
        panel_title = title if subtitle is None else f"{title}  [dim]{subtitle}[/dim]"
        console.print(Panel(body, title=panel_title, border_style="cyan"))
    else:
        print()
        print(f"  {title}" + (f"  {subtitle}" if subtitle else ""))
        print(body)


def render_retrieve(scope: str, query: str, response: Any) -> None:
    """Render retrieve results as readable terminal cards."""
    hits = list(response)
    if console:
        console.print()
        console.print(
            f"[bold cyan]ContextSeek[/bold cyan] [dim]· {scope}[/dim] retrieve"
        )
        console.print(f"[dim]query[/dim] {query!r}")
        console.print()

        if not hits:
            console.print("[yellow]No matching context found.[/yellow]")
            return

        for idx, hit in enumerate(hits, start=1):
            item = hit.item
            text = item.summary or item.content_text
            tier = _display_tier(hit.layer)
            source = item.provenance.source_id
            meta = Text()
            meta.append(f"score {hit.score:.3f}", style="green")
            meta.append(f" · {tier}", style="cyan")
            if source:
                meta.append(" · source: ", style="dim")
                meta.append(source, style="dim")
            meta.append("\n")
            meta.append(f"stage: {item.stage.value}", style="magenta")
            meta.append(f" · id: {item.id}", style="dim")
            if item.abstract:
                meta.append("\n")
                meta.append("L0 abstract: ", style="dim")
                meta.append(_clean_preview(item.abstract, max_chars=300), style="dim")

            body = Text(_clean_preview(text, max_chars=1200))
            console.print(
                Panel(
                    Group(meta, "", body),
                    title=f"Result {idx}",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        if response.meta.hint:
            console.print(
                Panel(response.meta.hint, title="Hint", border_style="yellow")
            )
        render_trace(getattr(response, "trace", None))
        return

    print()
    print(f"  ContextSeek · {scope}  retrieve")
    print(f"  query: {query!r}")
    print()
    if not hits:
        print("  No matching context found.")
        print()
        return
    for idx, hit in enumerate(hits, start=1):
        item = hit.item
        tier = _display_tier(hit.layer)
        source = item.provenance.source_id
        meta = f"score {hit.score:.3f} · {tier}"
        if source:
            meta += f" · source: {source}"
        print(f"  #{idx}  {meta}")
        print(f"      stage: {item.stage.value} · id: {item.id}")
        if item.abstract:
            print(f"      L0 abstract: {_clean_preview(item.abstract, max_chars=300)}")
        print(f"      {_clean_preview(item.summary or item.content_text)}")
        print()
    render_trace(getattr(response, "trace", None))


def render_trace(trace: Any) -> None:
    """Render the hierarchical retrieval descent (when present)."""
    if trace is None or not getattr(trace, "events", None):
        return
    lines: list[str] = []
    for ev in trace.events:
        depth = ev.scope.count("/") if ev.scope else 0
        indent = "  " * depth
        if ev.type == "descend":
            lines.append(f"{indent}→ {ev.scope}  (score {ev.score:.3f})")
        elif ev.type == "leaf_recall":
            n = ev.data.get("items", 0)
            if n:
                lines.append(f"{indent}    · collected {n} item(s)")
        elif ev.type == "converged":
            lines.append(f"  ⓘ {ev.message}")
    if not lines:
        return
    body = "\n".join(lines)
    if console:
        console.print(Panel(body, title="Retrieval trajectory", border_style="cyan"))
    else:
        print("  Retrieval trajectory:")
        for ln in lines:
            print("   " + ln)
        print()


def render_daemon_status(
    info: dict[str, Any], *, evolved: int | None = None, merged: int | None = None
) -> None:
    """Render daemon status in a consistent table."""
    if console:
        if info["running"]:
            title = f"ContextSeek daemon · running · PID {info['pid']}"
            subtitle = f"uptime {info.get('uptime')}" if info.get("uptime") else None
        else:
            title = "ContextSeek daemon · not running"
            subtitle = None

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Component")
        table.add_column("Status")
        for name, ok in info.get("components", {}).items():
            table.add_row(
                name, "[green]running[/green]" if ok else "[dim]stopped[/dim]"
            )
        if evolved is not None and merged is not None:
            table.add_row("Evolution (7d)", f"evolved={evolved}  merged={merged}")
        console.print(Panel(table, title=title, subtitle=subtitle, border_style="cyan"))
        return

    if info["running"]:
        print(f"  contextseek daemon  ·  running  (PID {info['pid']})")
        if info.get("uptime"):
            print(f"  uptime: {info['uptime']}")
        for k, v in info.get("components", {}).items():
            mark = "✓" if v else "✗"
            print(f"    {k:<24}  {mark}")
    else:
        print("  contextseek daemon  ·  not running")
    if evolved is not None and merged is not None:
        print("\n  Evolution stats (last 7 days)")
        print(f"    Evolved items: {evolved}  ·  Merged duplicates: {merged}")


@dataclass
class SyncProgress:
    """Progress renderer for `contextseek sync`."""

    _progress: Any = None
    _task: TaskID | None = None

    def __enter__(self) -> "SyncProgress":
        if console:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
                transient=False,
            )
            self._progress.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._progress is not None:
            self._progress.__exit__(exc_type, exc, tb)

    def update(self, added: int, skipped: int, total: int) -> None:
        if self._progress is None:
            if total == 0:
                print(
                    "\r  loading existing hashes ...              ", end="", flush=True
                )
                return
            done = added + skipped
            pct = int(done * 100 / total) if total else 100
            print(
                f"\r  [{pct:3d}%] {done}/{total}  added={added}  skipped={skipped}  ",
                end="",
                flush=True,
            )
            return

        if total == 0:
            if self._task is None:
                self._task = self._progress.add_task("Loading existing hashes", total=1)
            self._progress.update(self._task, completed=0)
            return
        if self._task is None:
            self._task = self._progress.add_task("Importing items", total=total)
        done = added + skipped
        self._progress.update(
            self._task,
            description=f"Importing items  added={added} skipped={skipped}",
            completed=done,
            total=total,
        )


def _clean_preview(text: str, *, max_chars: int = 900) -> str:
    clean = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(clean) > max_chars:
        return clean[: max_chars - 3].rstrip() + "..."
    return clean


def _display_tier(layer: str) -> str:
    """Map internal response layers to user-facing L0/L1/L2 labels."""
    if layer == "summary":
        return "L1"
    if layer == "full":
        return "L2"
    if layer == "abstract":
        return "L0"
    return layer
