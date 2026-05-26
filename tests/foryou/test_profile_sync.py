"""Signal richness + taste profile ensure/get."""
from __future__ import annotations

import pytest

from app.db import init_db
from app import profile_sync


@pytest.fixture(scope="module", autouse=True)
def _init():
    init_db()


def test_signal_richness_cold_warm_mature():
    score, state = profile_sync.compute_signal_richness(0, 0)
    assert state == "cold"
    score, state = profile_sync.compute_signal_richness(60, 20)
    # log10(60/30) + log10(20/10) ≈ 0.30 + 0.30 = 0.60 → warm
    assert state == "warm"
    score, state = profile_sync.compute_signal_richness(500, 100)
    assert state == "mature"


def test_ensure_taste_profile_idempotent():
    profile_sync.ensure_taste_profile("uuid-a")
    profile_sync.ensure_taste_profile("uuid-a")
    tp = profile_sync.get_taste_profile("uuid-a")
    assert tp is not None
    assert tp["profile_uuid"] == "uuid-a"


def test_privacy_mode_update():
    profile_sync.ensure_taste_profile("uuid-b")
    profile_sync.set_privacy_mode("uuid-b", "fortress")
    tp = profile_sync.get_taste_profile("uuid-b")
    assert tp["privacy_mode"] == "fortress"
