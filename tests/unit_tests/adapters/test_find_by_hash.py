"""Tests for SeekVFSAdapter.find_by_hash fast-path used by add() conflict checks."""

from __future__ import annotations

import json

from seekvfs import VFS

from contextseek.storage.in_memory_backend import InMemoryBackend
from contextseek.storage.storage_adapter import SeekVFSStorageAdapter
from contextseek.storage.tiered_adapter import TieredSeekVFSAdapter
from contextseek.storage.vector_memory_adapter import VectorMemoryAdapter


def _make_adapter() -> SeekVFSStorageAdapter:
    vfs = VFS(
        routes={"contextseek://": {"backend": InMemoryBackend()}},
        scheme="contextseek://",
    )
    return SeekVFSStorageAdapter(vfs)


class TestInMemoryBackendFindByHash:
    def test_returns_path_for_known_hash(self) -> None:
        adapter = _make_adapter()
        adapter.write("contextseek://t/p/a", {"hash": "abc", "content": "x"})
        adapter.write("contextseek://t/p/b", {"hash": "def", "content": "y"})

        assert (
            adapter.find_by_hash("contextseek://t/p/", "abc") == "contextseek://t/p/a"
        )
        assert (
            adapter.find_by_hash("contextseek://t/p/", "def") == "contextseek://t/p/b"
        )

    def test_returns_none_for_unknown_hash(self) -> None:
        adapter = _make_adapter()
        adapter.write("contextseek://t/p/a", {"hash": "abc", "content": "x"})
        assert adapter.find_by_hash("contextseek://t/p/", "missing") is None

    def test_prefix_isolation(self) -> None:
        adapter = _make_adapter()
        adapter.write("contextseek://t/p/a", {"hash": "shared", "content": "x"})
        # Prefix mismatch: hash exists but lives in a different scope.
        assert adapter.find_by_hash("contextseek://t/other/", "shared") is None

    def test_overwrite_updates_index(self) -> None:
        adapter = _make_adapter()
        adapter.write("contextseek://t/p/a", {"hash": "old", "content": "v1"})
        adapter.write("contextseek://t/p/a", {"hash": "new", "content": "v2"})
        # Old hash mapping is cleared; new hash points to the same path.
        assert adapter.find_by_hash("contextseek://t/p/", "old") is None
        assert (
            adapter.find_by_hash("contextseek://t/p/", "new") == "contextseek://t/p/a"
        )

    def test_delete_clears_index(self) -> None:
        adapter = _make_adapter()
        adapter.write("contextseek://t/p/a", {"hash": "abc", "content": "x"})
        adapter.delete("contextseek://t/p/a")
        assert adapter.find_by_hash("contextseek://t/p/", "abc") is None

    def test_payload_without_hash_does_not_break_lookup(self) -> None:
        adapter = _make_adapter()
        adapter.write("contextseek://t/p/a", {"content": "no hash"})
        assert adapter.find_by_hash("contextseek://t/p/", "anything") is None


class TestVectorMemoryAdapterFindByHash:
    def test_find_by_hash(self) -> None:
        adapter = VectorMemoryAdapter()
        adapter.write("contextseek://t/p/a", {"hash": "abc", "content": "x"})
        adapter.write("contextseek://t/p/b", {"hash": "def", "content": "y"})

        assert (
            adapter.find_by_hash("contextseek://t/p/", "abc") == "contextseek://t/p/a"
        )
        assert adapter.find_by_hash("contextseek://t/p/", "missing") is None
        assert adapter.find_by_hash("contextseek://t/other/", "abc") is None


class TestTieredAdapterFindByHash:
    def test_hot_hit_short_circuits(self) -> None:
        hot = _make_adapter()
        cold = _make_adapter()
        tiered = TieredSeekVFSAdapter(hot=hot, cold=cold)
        tiered.write("contextseek://t/p/a", {"hash": "abc", "content": "x"})
        assert tiered.find_by_hash("contextseek://t/p/", "abc") == "contextseek://t/p/a"

    def test_cold_fallback(self) -> None:
        hot = _make_adapter()
        cold = _make_adapter()
        tiered = TieredSeekVFSAdapter(hot=hot, cold=cold)
        tiered.write(
            "contextseek://t/p/a", {"hash": "abc", "content": "x", "tier": "cold"}
        )
        # Hot stub may exist but its hash is the same; tiered should still
        # resolve via hot first. Test the explicit cold-only path:
        cold.write("contextseek://t/p/b", {"hash": "only-cold", "content": "y"})
        assert (
            tiered.find_by_hash("contextseek://t/p/", "only-cold")
            == "contextseek://t/p/b"
        )

    def test_unknown_hash_returns_none(self) -> None:
        hot = _make_adapter()
        cold = _make_adapter()
        tiered = TieredSeekVFSAdapter(hot=hot, cold=cold)
        assert tiered.find_by_hash("contextseek://t/p/", "missing") is None


class TestPayloadHashSerialized:
    """Sanity check: ContextItem serialisation must include the hash field
    (otherwise the InMemory backend's hash index never populates)."""

    def test_serialized_payload_carries_hash(self) -> None:
        from contextseek.domain.context_item import ContextItem
        from contextseek.domain.provenance import Provenance, SourceType
        from contextseek.domain.serialization import serialize_context_item

        item = ContextItem(
            content="hello",
            scope="t/p",
            provenance=Provenance(
                source_type=SourceType.human_input,
                source_id="s",
                confidence=0.5,
            ),
        )
        payload = serialize_context_item(item)
        assert payload["hash"] == item.hash
        # And it survives a json round-trip (in-memory backend stores bytes).
        roundtripped = json.loads(json.dumps(payload, default=str))
        assert roundtripped["hash"] == item.hash
