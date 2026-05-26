"""Candidate upsert / fetch / mark_surfaced; DB init."""
from __future__ import annotations

import struct

import pytest

from app import candidates
from app.db import init_db, cursor


@pytest.fixture(scope="module", autouse=True)
def _init():
    init_db()


def test_upsert_and_fetch():
    candidates.upsert({"video_id": "vid1", "title": "Hello", "channel_id": "Cx"}, source="rss_test")
    rows = candidates.by_source("rss_test")
    assert any(r["video_id"] == "vid1" for r in rows)


def test_upsert_idempotent_and_preserves_old_fields():
    candidates.upsert({"video_id": "vid2", "title": "Original", "channel_id": "C1",
                       "duration_seconds": 120}, source="rss_test")
    candidates.upsert({"video_id": "vid2", "title": "Updated"}, source="rss_test")
    got = candidates.fetch_many(["vid2"])["vid2"]
    assert got["title"] == "Updated"
    assert got["channel_id"] == "C1"  # preserved
    assert got["duration_seconds"] == 120


def test_embedding_round_trip():
    candidates.upsert({"video_id": "vid3", "title": "Emb"}, source="rss_test")
    candidates.set_embedding("vid3", [0.1, 0.2, 0.3])
    got = candidates.fetch_many(["vid3"])["vid3"]
    vec = candidates.unpack_embedding(got["embedding"])
    assert len(vec) == 3
    assert abs(vec[0] - 0.1) < 1e-5


def test_mark_surfaced_updates_last_used_at():
    candidates.upsert({"video_id": "vid4", "title": "S"}, source="rss_test")
    candidates.mark_surfaced(["vid4"])
    got = candidates.fetch_many(["vid4"])["vid4"]
    assert got["last_used_at"] is not None
