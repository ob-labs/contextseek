"""Generic helpers for SDK plug proxies."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from contextseek.client.contextseek import ContextSeek
from contextseek.plugs.core.gateway import PlugGateway
from contextseek.plugs.core.protocols import MaterializationReceipt, PlugChangeEvent


logger = logging.getLogger(__name__)


@dataclass
class SDKProxyBase:
    """Shared materialization behavior for SDK drop-in wrappers."""

    client: ContextSeek | None = None
    max_retry: int = 3
    strict_contextseek: bool = False

    @property
    def contextseek_client(self) -> ContextSeek:
        if self.client is None:
            self.client = ContextSeek.from_settings()
        return self.client

    def materialize_events(
        self,
        events: list[PlugChangeEvent],
    ) -> list[MaterializationReceipt]:
        if not events:
            return []
        gateway = PlugGateway(self.contextseek_client, max_retry=self.max_retry)
        receipts: list[MaterializationReceipt] = []
        for event in events:
            try:
                receipts.append(gateway.apply(event))
            except Exception:
                if self.strict_contextseek:
                    raise
                logger.exception("failed to materialize SDK plug event")
        return receipts
