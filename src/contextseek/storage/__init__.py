"""ContextSeek storage backends (VFS, file, OceanBase, tiered)."""

from __future__ import annotations

from contextseek.storage.file_backend import FileBackend
from contextseek.storage.in_memory_backend import InMemoryBackend
from contextseek.storage.protocol import SeekVFSAdapter
from contextseek.storage.protocol import VectorSearchMixin
from contextseek.storage.storage_adapter import SeekVFSStorageAdapter
from contextseek.storage.tiered_adapter import TieredSeekVFSAdapter
from contextseek.storage.vector_memory_adapter import VectorMemoryAdapter

__all__ = [
    "SeekVFSAdapter",
    "GeoSearchMixin",
    "VectorSearchMixin",
    "InMemoryBackend",
    "FileBackend",
    "SeekVFSStorageAdapter",
    "TieredSeekVFSAdapter",
    "VectorMemoryAdapter",
    "OceanBaseBackend",
    "OceanBaseGeoBackend",
]


def __getattr__(name: str):
    """Load optional backends only when explicitly requested."""
    if name == "OceanBaseBackend":
        from contextseek.storage.ob_backend import OceanBaseBackend

        return OceanBaseBackend
    if name == "OceanBaseGeoBackend":
        from contextseek.storage.ob_geo_backend import OceanBaseGeoBackend

        return OceanBaseGeoBackend
    if name == "GeoSearchMixin":
        from contextseek.storage.protocol import GeoSearchMixin

        return GeoSearchMixin
    raise AttributeError(name)
