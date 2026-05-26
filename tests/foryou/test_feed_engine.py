"""Feed-engine CRUD + generation with mocked sources."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app import candidates, profile_sync
from app.db import init_db
from app.feeds import engine, spec


@pytest.fixture(scope="module", autouse=True)
def _init():
    init_db()
    profile_sync.ensure_taste_profile("uuid-feed-test")


def test_default_feeds_created_once():
    engine.ensure_default_feeds("uuid-feed-test")
    feeds1 = engine.list_feeds("uuid-feed-test")
    engine.ensure_default_feeds("uuid-feed-test")  # idempotent
    feeds2 = engine.list_feeds("uuid-feed-test")
    assert len(feeds1) == len(feeds2)
    kinds = {f["kind"] for f in feeds1}
    assert spec.KIND_QUALITY in kinds
    assert spec.KIND_DISCOVER in kinds


def test_create_and_delete_feed():
    fid = engine.create_feed("uuid-feed-test", spec.KIND_FRESH, label="Custom")
    feeds = engine.list_feeds("uuid-feed-test")
    assert any(f["feed_id"] == fid for f in feeds)
    engine.delete_feed("uuid-feed-test", fid)
    feeds2 = engine.list_feeds("uuid-feed-test")
    assert all(f["feed_id"] != fid for f in feeds2)


def test_get_feed_items_empty_when_no_snapshot():
    fid = engine.create_feed("uuid-feed-test", spec.KIND_FRESH, label="Empty")
    out = engine.get_feed_items(fid)
    assert out["videos"] == []
    assert out["generated_at"] == 0


@pytest.mark.asyncio
async def test_generate_quality_feed_uses_tournesol_candidates():
    # Pre-seed candidates and patch the source so we don't hit the network.
    candidates.upsert({"video_id": "qv1", "title": "Quality A", "quality_score": 90}, source="tournesol")
    candidates.upsert({"video_id": "qv2", "title": "Quality B", "quality_score": 85}, source="tournesol")
    fid = engine.create_feed("uuid-feed-test", spec.KIND_QUALITY, label="Q")

    with patch("app.feeds.engine.TournesolSource") as Src:
        instance = Src.return_value
        instance.fetch_top = AsyncMock(return_value=2)
        # Avoid PYTR HTTP call.
        with patch("app.feeds.engine.fetch_profile_export", AsyncMock(return_value={
            "favorites": [], "history": [], "followed_channels": [],
            "profile_meta": {"content_lang": "auto"}})):
            n = await engine.generate_feed(fid, mode="balanced")
    assert n >= 1
    items = engine.get_feed_items(fid)
    ids = {v["video_id"] for v in items["videos"]}
    assert "qv1" in ids
