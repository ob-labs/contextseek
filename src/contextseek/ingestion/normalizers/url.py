"""URL normalizer."""

from __future__ import annotations

from typing import Any

from contextseek.ingestion.models import RawEvent
from contextseek.ingestion.normalizers.base import normalize_base


class UrlNormalizer:
    def normalize(
        self, payload: dict[str, Any], *, connector_id: str, partition: str
    ) -> RawEvent:
        payload = dict(payload)
        payload.setdefault("scope", payload.get("scope", "url/default"))
        return normalize_base(
            payload,
            connector_id=connector_id,
            partition=partition,
            source_type="url_doc",
        )

