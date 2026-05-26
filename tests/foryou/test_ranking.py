"""Cosine, MMR, exploration_floor, stratified_sample."""
from __future__ import annotations

import math

from app import ranking


def test_cosine_basic():
    assert math.isclose(ranking.cosine([1, 0], [1, 0]), 1.0)
    assert math.isclose(ranking.cosine([1, 0], [0, 1]), 0.0)
    assert math.isclose(ranking.cosine([1, 0], [-1, 0]), -1.0)


def test_cosine_handles_empty():
    assert ranking.cosine([], [1]) == 0.0
    assert ranking.cosine([1], [0]) == 0.0


def test_centroid_weighted():
    c = ranking.centroid([[1, 0], [0, 1]], weights=[1, 3])
    assert math.isclose(c[0], 0.25)
    assert math.isclose(c[1], 0.75)


def test_mmr_diversifies_off_one_channel():
    items = [
        {"video_id": "a", "embedding": [1, 0], "channel_id": "X"},
        {"video_id": "b", "embedding": [0.95, 0.1], "channel_id": "X"},
        {"video_id": "c", "embedding": [0.9, 0.2], "channel_id": "X"},
        {"video_id": "d", "embedding": [0, 1], "channel_id": "Y"},
    ]
    out = ranking.mmr(items, [1, 0], lam=0.5, k=3, channel_quota=2)
    ids = [it["video_id"] for it in out]
    # First pick is most relevant; channel X capped at 2.
    assert ids[0] == "a"
    assert ids.count("a") == 1
    chans = [it["channel_id"] for it in out]
    assert chans.count("X") <= 2


def test_exploration_floor_never_below_25pct():
    assert ranking.exploration_floor(1.0) == 0.25
    assert ranking.exploration_floor(0.0) > 0.5
    # Monotone decrease.
    a = ranking.exploration_floor(0.1)
    b = ranking.exploration_floor(0.6)
    assert a > b >= 0.25


def test_stratified_sample_round_robin():
    out = ranking.stratified_sample({"a": [1, 2], "b": [3, 4, 5], "c": [6]}, k=5)
    assert out == [1, 3, 6, 2, 4]
