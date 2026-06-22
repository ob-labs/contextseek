"""Unit tests for HTTP API facade (`create_app`)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

pytest.importorskip("fastapi", reason="http extra not installed")

from contextseek.domain.context_item import ContextItem
from contextseek.domain.provenance import Provenance, SourceType
from contextseek.domain.results import ResponseMeta, RetrieveResponse, SearchHit
from contextseek.http.server import create_app


def _asgi_post(app, path: str, **kwargs) -> httpx.Response:
    async def _request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post(path, **kwargs)

    return asyncio.run(_request())


def _sample_hit() -> SearchHit:
    item = ContextItem(
        id="item-1",
        scope="tenant/project/session",
        content="full body",
        summary="short summary",
        tags=["ops"],
        provenance=Provenance(
            source_type=SourceType.document,
            source_id="doc://sample",
            confidence=0.8,
        ),
    )
    return SearchHit(
        item=item,
        score=0.91,
        layer="summary",
        provenance_summary="from sample doc",
        stage_confidence=0.85,
        recall_path="phrase",
    )


def test_http_retrieve_forwards_include_expired_and_returns_meta() -> None:
    ctx = MagicMock(name="ContextSeek")
    ctx.retrieve.return_value = RetrieveResponse(
        items=[_sample_hit()],
        meta=ResponseMeta(layer="summary", full_via="expand", hint="use expand"),
    )
    app = create_app(client=ctx)

    res = _asgi_post(
        app,
        "/retrieve",
        json={
            "scope": "tenant/project/session",
            "query": "deploy note",
            "k": 5,
            "include_deleted": False,
            "include_expired": True,
        },
    )

    assert res.status_code == 200
    ctx.retrieve.assert_called_once_with(
        "deploy note",
        scope="tenant/project/session",
        k=5,
        full=False,
        filters=None,
        include_deleted=False,
        include_expired=True,
    )
    body = res.json()
    assert body["_meta"] == {
        "layer": "summary",
        "full_via": "expand",
        "hint": "use expand",
    }
    assert body["items"][0]["id"] == "item-1"
    assert body["items"][0]["content"] is None


def test_http_compact_returns_conflict_counts() -> None:
    ctx = MagicMock(name="ContextSeek")
    ctx.compact.return_value = SimpleNamespace(
        merged_count=1,
        archived_count=2,
        evolved_count=3,
        conflict_updated_count=4,
        conflict_drift_count=5,
    )
    app = create_app(client=ctx)

    res = _asgi_post(
        app,
        "/compact",
        json={
            "scope": "tenant/project/session",
            "dry_run": True,
        },
    )

    assert res.status_code == 200
    ctx.compact.assert_called_once_with(scope="tenant/project/session", dry_run=True)
    assert res.json() == {
        "merged": 1,
        "archived": 2,
        "evolved": 3,
        "conflict_updated": 4,
        "conflict_drift": 5,
    }
