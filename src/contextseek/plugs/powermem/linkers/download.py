"""Shared download helpers for PowerMem linker assets."""

from __future__ import annotations

import ssl
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Any


def urlopen_with_certifi(
    request: str | urllib.request.Request,
    *,
    timeout: float,
) -> Any:
    """Open URLs with certifi CA for HTTPS downloads when available."""
    context = _ssl_context_for_request(request)
    if context is None:
        return urllib.request.urlopen(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout, context=context)


def _ssl_context_for_request(
    request: str | urllib.request.Request,
) -> ssl.SSLContext | None:
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    if urllib.parse.urlparse(url).scheme.lower() != "https":
        return None
    return _certifi_ssl_context()


@lru_cache(maxsize=1)
def _certifi_ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())
