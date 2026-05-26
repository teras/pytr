"""Privacy modes + egress allowlist."""
from __future__ import annotations

import pytest

from app import privacy


def test_fortress_allows_only_safe_purposes():
    dec = privacy.check_egress("https://api.tournesol.app/foo", "tournesol", "fortress")
    assert dec.allowed, dec.reason
    dec = privacy.check_egress("https://www.youtube.com/feeds/videos.xml?channel_id=UCabc", "channel_rss", "fortress")
    assert dec.allowed


def test_fortress_blocks_yt_search_and_cloud():
    assert not privacy.check_egress("https://www.youtube.com/results?q=foo", "yt_search", "fortress").allowed
    assert not privacy.check_egress("https://api.openai.com/v1/foo", "llm", "fortress").allowed


def test_balanced_allows_yt_and_community_but_not_cloud():
    assert privacy.check_egress("https://www.youtube.com/results", "yt_search", "balanced").allowed
    assert privacy.check_egress("https://www.reddit.com/r/videos/top.json", "community_picks", "balanced").allowed
    assert not privacy.check_egress("https://api.openai.com/v1/foo", "llm", "balanced").allowed


def test_cloud_allows_llm():
    assert privacy.check_egress("https://api.anthropic.com/v1/messages", "llm", "cloud").allowed
    assert privacy.check_egress("https://generativelanguage.googleapis.com/v1beta", "llm", "cloud").allowed


def test_unknown_host_blocked():
    assert not privacy.check_egress("https://evil.example/spy", "llm", "cloud").allowed


def test_subdomain_match():
    dec = privacy.check_egress("https://cdn.musicbrainz.org/ws/2/artist/", "musicbrainz", "fortress")
    assert dec.allowed


def test_payload_sanitiser_drops_identifiers():
    raw = {
        "profile_uuid": "abc",
        "favorites_list": [1, 2],
        "topic": "jazz",
        "nested": {"user_id": 7, "label": "ok"},
    }
    clean = privacy.sanitize_payload(raw)
    assert "profile_uuid" not in clean
    assert "favorites_list" not in clean
    assert clean["topic"] == "jazz"
    assert "user_id" not in clean["nested"]
    assert clean["nested"]["label"] == "ok"


def test_hf_runtime_blocked_when_setup_closed():
    privacy.close_hf_for_setup()
    assert not privacy.check_egress("https://huggingface.co/model.bin", "model_setup", "fortress").allowed


def test_hf_open_window_works():
    privacy.open_hf_for_setup()
    try:
        assert privacy.check_egress("https://huggingface.co/model.bin", "model_setup", "fortress").allowed
    finally:
        privacy.close_hf_for_setup()
