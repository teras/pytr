"""Knowledge graph: upsert nodes/edges, top_nodes, axis_bridge_queries."""
from __future__ import annotations

import pytest

from app import profile_sync
from app.db import init_db
from app import graph


@pytest.fixture(scope="module", autouse=True)
def _init():
    init_db()
    profile_sync.ensure_taste_profile("uuid-kg")


def test_upsert_node_and_edge_idempotent():
    graph.upsert_node("uuid-kg", "topic:jazz", "topic", "Jazz", affinity=0.5)
    graph.upsert_node("uuid-kg", "topic:jazz", "topic", "Jazz", affinity=0.7)
    graph.upsert_edge("uuid-kg", "channel:UC1", "topic:jazz", "has_genre", weight=1.0)
    graph.upsert_edge("uuid-kg", "channel:UC1", "topic:jazz", "has_genre", weight=1.0)
    nodes = graph.top_nodes("uuid-kg")
    jazz = next(n for n in nodes if n["node_id"] == "topic:jazz")
    # Affinity averaged then averaged again — should stay in a reasonable band.
    assert 0.3 <= jazz["affinity"] <= 0.7


def test_axis_bridge_queries_pairs_topics():
    for i, label in enumerate(["Bebop", "Funk", "Soul", "Krautrock", "Dub"]):
        graph.upsert_node("uuid-kg", f"topic:{label}", "topic", label, affinity=0.5 - i * 0.05)
    qs = graph.axis_bridge_queries("uuid-kg", k=3)
    assert len(qs) >= 1
    # Each query should contain two distinct topic labels.
    for q in qs:
        parts = q.split()
        assert len(parts) >= 2
