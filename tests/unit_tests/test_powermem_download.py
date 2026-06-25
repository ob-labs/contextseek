"""Tests for PowerMem asset download helpers."""

from __future__ import annotations

import sys
from argparse import Namespace
from typing import Any

from contextseek.plugs.powermem.linkers import download


class _Response:
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def test_urlopen_with_certifi_uses_certifi_ca_for_https(monkeypatch) -> None:
    download._certifi_ssl_context.cache_clear()  # noqa: SLF001
    monkeypatch.setitem(
        sys.modules,
        "certifi",
        Namespace(where=lambda: "/tmp/cacert.pem"),
    )
    monkeypatch.setattr(
        download.ssl,
        "create_default_context",
        lambda *, cafile: f"ssl-context:{cafile}",
    )
    calls: list[dict[str, Any]] = []

    def fake_urlopen(request, *, timeout, context=None):
        calls.append({"request": request, "timeout": timeout, "context": context})
        return _Response()

    monkeypatch.setattr(download.urllib.request, "urlopen", fake_urlopen)

    with download.urlopen_with_certifi("https://example.test/pkg.tgz", timeout=3):
        pass

    assert calls == [
        {
            "request": "https://example.test/pkg.tgz",
            "timeout": 3,
            "context": "ssl-context:/tmp/cacert.pem",
        }
    ]


def test_urlopen_with_certifi_leaves_non_https_without_context(monkeypatch) -> None:
    download._certifi_ssl_context.cache_clear()  # noqa: SLF001
    calls: list[dict[str, Any]] = []

    def fake_urlopen(request, *, timeout, context=None):
        calls.append({"request": request, "timeout": timeout, "context": context})
        return _Response()

    monkeypatch.setattr(download.urllib.request, "urlopen", fake_urlopen)

    with download.urlopen_with_certifi("file:///tmp/pkg.zip", timeout=3):
        pass

    assert calls == [
        {
            "request": "file:///tmp/pkg.zip",
            "timeout": 3,
            "context": None,
        }
    ]
