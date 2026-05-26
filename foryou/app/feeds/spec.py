# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Feed spec definitions — one engine interprets all 7 feed kinds."""
from __future__ import annotations

KIND_FRESH = "fresh"
KIND_QUALITY = "quality"
KIND_TRENDING = "trending"
KIND_CLOSER = "closer"
KIND_DISCOVER = "discover"
KIND_COMMUNITY = "community"
KIND_SURPRISE = "surprise"

ALL_KINDS = [KIND_FRESH, KIND_QUALITY, KIND_TRENDING, KIND_CLOSER, KIND_DISCOVER, KIND_COMMUNITY, KIND_SURPRISE]

# Default configs per kind. Stored per-feed JSON overrides at creation time.
DEFAULT_CONFIG = {
    KIND_FRESH: {
        "sources": ["channel_rss"],
        "rerank": "chronological",
        "channel_quota": None,
        "size": 30,
    },
    KIND_QUALITY: {
        "sources": ["tournesol"],
        "rerank": "quality_score",
        "channel_quota": 3,
        "size": 30,
    },
    KIND_TRENDING: {
        "sources": ["yt_charts"],
        "rerank": "view_count",
        "channel_quota": 2,
        "size": 30,
    },
    KIND_CLOSER: {
        # Pure exploit: similarity to favorites centroid + uploads from followed channels.
        "sources": ["channel_rss", "yt_related", "yt_search"],
        "rerank": "mmr",
        "mmr_lambda": 0.9,
        "channel_quota": 3,
        "exploration_pct": 0.0,
        "size": 30,
    },
    KIND_DISCOVER: {
        "sources": ["yt_search", "tournesol", "channel_rss"],
        "rerank": "mmr",
        "mmr_lambda": 0.7,
        "channel_quota": 2,
        "exploration_floor": 0.25,
        "size": 30,
    },
    KIND_COMMUNITY: {
        "sources": ["reddit", "hn_algolia"],
        "rerank": "chronological",
        "channel_quota": 2,
        "size": 30,
    },
    KIND_SURPRISE: {
        "sources": ["yt_search", "tournesol", "community_picks"],
        "rerank": "mmr",
        "mmr_lambda": 0.4,
        "channel_quota": 1,
        "exploration_floor": 0.6,
        "oblique_llm": True,
        "size": 30,
    },
}


def labels() -> dict[str, str]:
    return {
        KIND_FRESH: "Fresh",
        KIND_QUALITY: "Quality",
        KIND_TRENDING: "Trending",
        KIND_CLOSER: "Closer",
        KIND_DISCOVER: "Discover",
        KIND_COMMUNITY: "Community Picks",
        KIND_SURPRISE: "Surprise me",
    }
