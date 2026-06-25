"""Unit tests for the SQLite storage backend."""

from __future__ import annotations

import json

import pytest

from contextseek.storage.sqlite_backend import SQLiteBackend
from seekvfs.exceptions import NotFoundError


def _stub_ef(texts: list[str]) -> list[list[float]]:
    """Deterministic 3-dim embedder: counts of 'a', 'b', 'c' (no model download)."""
    return [
        [
            float(t.lower().count("a")),
            float(t.lower().count("b")),
            float(t.lower().count("c")),
        ]
        for t in texts
    ]


def _item(scope: str, abstract: str, content: str, hash_: str) -> str:
    return json.dumps(
        {
            "abstract": abstract,
            "summary": "",
            "content": content,
            "scope": scope,
            "stage": "",
            "searchable": True,
            "hash": hash_,
        }
    )


@pytest.fixture()
def backend(tmp_path) -> SQLiteBackend:
    b = SQLiteBackend(path=str(tmp_path / "t.sqlite3"), embedding_function=_stub_ef)
    b.initialize()
    yield b
    b.close()


def test_write_read_roundtrip(backend: SQLiteBackend) -> None:
    backend.write(
        "contextseek://me/work/a", _item("me/work", "abstract a", "body", "h1")
    )
    payload = json.loads(backend.read("contextseek://me/work/a").content.decode())
    assert payload["abstract"] == "abstract a"
    assert payload["scope"] == "me/work"
    assert payload["hash"] == "h1"


def test_read_missing_raises(backend: SQLiteBackend) -> None:
    with pytest.raises(NotFoundError):
        backend.read("contextseek://nope")


def test_ls_scoped_and_recursive(backend: SQLiteBackend) -> None:
    backend.write("contextseek://me/work/a", _item("me/work", "a", "x", "h1"))
    backend.write("contextseek://me/work/sub/b", _item("me/work", "b", "x", "h2"))
    backend.write("contextseek://me/home/c", _item("me/home", "c", "x", "h3"))
    top = [f.path for f in backend.ls("contextseek://me/work/")]
    assert top == ["contextseek://me/work/a"]
    rec = [f.path for f in backend.ls("contextseek://me/work/", recursive=True)]
    assert "contextseek://me/work/sub/b" in rec


def test_find_by_hash(backend: SQLiteBackend) -> None:
    backend.write("contextseek://me/work/a", _item("me/work", "a", "x", "hX"))
    assert backend.find_by_hash("contextseek://*", "hX") == "contextseek://me/work/a"
    assert backend.find_by_hash("contextseek://*", "missing") is None


def test_fts_search(backend: SQLiteBackend) -> None:
    backend.write(
        "contextseek://me/w/a", _item("me/w", "deployment crashed", "oom", "h1")
    )
    backend.write("contextseek://me/w/b", _item("me/w", "budget review", "money", "h2"))
    hits = backend.search("deployment", path_pattern="contextseek://me/w/*", limit=5)
    assert hits.hits and hits.hits[0].path == "contextseek://me/w/a"


def test_vector_search(backend: SQLiteBackend) -> None:
    backend.write("contextseek://s/aaa", _item("s", "aaa", "x", "h1"))
    backend.write("contextseek://s/ccc", _item("s", "ccc", "x", "h2"))
    qv = _stub_ef(["aaaa"])[0]
    hits = backend.search(
        "", path_pattern="contextseek://s/*", limit=5, query_embedding=qv
    )
    assert hits.hits[0].path == "contextseek://s/aaa"


def test_update_without_embedding_preserves_existing_vector(tmp_path) -> None:
    backend = SQLiteBackend(path=str(tmp_path / "t.sqlite3"))
    backend.initialize()
    try:
        payload = json.loads(_item("s", "aaa", "x", "h1"))
        payload["embedding"] = [1.0, 0.0, 0.0]
        backend.write("contextseek://s/aaa", json.dumps(payload))

        payload.pop("embedding")
        payload["content"] = "x touched"
        backend.write("contextseek://s/aaa", json.dumps(payload))

        hits = backend.search(
            "",
            path_pattern="contextseek://s/*",
            limit=5,
            query_embedding=[1.0, 0.0, 0.0],
        )
    finally:
        backend.close()

    assert hits.hits[0].path == "contextseek://s/aaa"


def test_delete(backend: SQLiteBackend) -> None:
    backend.write("contextseek://me/x", _item("me", "a", "x", "h1"))
    backend.delete("contextseek://me/x")
    with pytest.raises(NotFoundError):
        backend.read("contextseek://me/x")
    with pytest.raises(NotFoundError):
        backend.delete("contextseek://me/x")


def test_sync_tables(backend: SQLiteBackend) -> None:
    backend.sync_hash_add("me/w", "h1")
    backend.sync_hashes_add_batch("me/w", {"h2", "h3"})
    assert backend.sync_hashes_for_scope("me/w") == {"h1", "h2", "h3"}
    backend.sync_file_record("me/w", "/a.txt", 1.5, "ch")
    assert backend.sync_files_for_scope("me/w") == {"/a.txt": (1.5, "ch")}
    backend.meta_set("k", "v")
    assert backend.meta_get("k") == "v"


def test_visible_count(backend: SQLiteBackend) -> None:
    backend.write("contextseek://me/w/a", _item("me/w", "a", "x", "h1"))
    backend.write("contextseek://me/w/b", _item("me/w", "b", "x", "h2"))
    assert backend.visible_count_for_scope("me/w") == 2
