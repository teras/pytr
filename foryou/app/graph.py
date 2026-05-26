# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-profile knowledge graph.

Built lazily from PYTR's favorites + watch history, enriched via Wikidata
when available. Exposed as an 'axis-bridge' bucket type that the Discover
generator can sample from. Promotion to default Discover is a separate
business decision (A/B over hit-rate); for now it only augments.
"""
from __future__ import annotations

import json
import logging
import time

from .adapters.enrichment import WikidataAdapter
from .db import cursor
from .profile_sync import fetch_profile_export

log = logging.getLogger(__name__)


def upsert_node(profile_uuid: str, node_id: str, node_type: str, label: str,
                language: str | None = None, affinity: float = 0.5,
                metadata: dict | None = None):
    now = int(time.time())
    with cursor(write=True) as c:
        c.execute(
            "INSERT INTO kg_nodes (profile_uuid, node_id, node_type, label, language, "
            " affinity, first_seen_at, last_seen_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(profile_uuid, node_id) DO UPDATE SET "
            " affinity = (kg_nodes.affinity + excluded.affinity) / 2.0, "
            " last_seen_at = excluded.last_seen_at, "
            " label = excluded.label, "
            " metadata = COALESCE(excluded.metadata, kg_nodes.metadata)",
            (profile_uuid, node_id, node_type, label, language, affinity,
             now, now, json.dumps(metadata) if metadata else None),
        )


def upsert_edge(profile_uuid: str, from_node: str, to_node: str,
                edge_type: str, weight: float = 1.0):
    now = int(time.time())
    with cursor(write=True) as c:
        c.execute(
            "INSERT INTO kg_edges (profile_uuid, from_node, to_node, edge_type, weight, last_updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(profile_uuid, from_node, to_node, edge_type) DO UPDATE SET "
            " weight = kg_edges.weight + excluded.weight, last_updated_at = excluded.last_updated_at",
            (profile_uuid, from_node, to_node, edge_type, weight, now),
        )


def top_nodes(profile_uuid: str, limit: int = 50, node_type: str | None = None) -> list[dict]:
    with cursor() as c:
        if node_type:
            rows = c.execute(
                "SELECT * FROM kg_nodes WHERE profile_uuid=? AND node_type=? "
                "ORDER BY affinity DESC LIMIT ?",
                (profile_uuid, node_type, limit)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM kg_nodes WHERE profile_uuid=? ORDER BY affinity DESC LIMIT ?",
                (profile_uuid, limit)).fetchall()
    return [dict(r) for r in rows]


async def rebuild_graph(profile_uuid: str, mode: str = "balanced"):
    """Rebuild from PYTR favorites + history; enrich each channel via Wikidata."""
    export = await fetch_profile_export(profile_uuid) or {}
    favs = export.get("favorites") or []
    hist = export.get("history") or []
    wd = WikidataAdapter()
    seen_channels: dict[str, float] = {}
    for f in favs:
        cid = f.get("channel_id")
        if cid:
            seen_channels[cid] = seen_channels.get(cid, 0.0) + 1.0  # favorite weight
    for h in hist[:500]:
        cid = h.get("channel_id")
        if cid:
            seen_channels[cid] = seen_channels.get(cid, 0.0) + 0.3
    for cid, affinity in seen_channels.items():
        upsert_node(profile_uuid, f"channel:{cid}", "channel",
                    label=cid, affinity=min(1.0, affinity / 5.0))
        info = await wd.for_channel(cid, mode=mode)
        if not info or info.get("empty"):
            continue
        # Add topic nodes for occupations + genres, edged to the channel.
        for occ in info.get("occupations") or []:
            nid = f"topic:occ:{occ.lower()}"
            upsert_node(profile_uuid, nid, "topic", label=occ, affinity=0.4)
            upsert_edge(profile_uuid, f"channel:{cid}", nid, "is_a")
        for genre in info.get("genres") or []:
            nid = f"topic:genre:{genre.lower()}"
            upsert_node(profile_uuid, nid, "topic", label=genre, affinity=0.5)
            upsert_edge(profile_uuid, f"channel:{cid}", nid, "has_genre")
    log.info("kg rebuild done for %s — channels=%d", profile_uuid, len(seen_channels))


def axis_bridge_queries(profile_uuid: str, k: int = 5) -> list[str]:
    """Return graph-derived search queries that bridge the user's top topics.

    Cheap recipe: take the top-N topic nodes (excluding the user's mainstream ones)
    and pair adjacent ones. The bucket sampler in Discover treats these as an
    extra exploration bucket.
    """
    topics = top_nodes(profile_uuid, limit=20, node_type="topic")
    if len(topics) < 2:
        return []
    # Pair items at indices [k-1, k] to lean toward bridge / mid-affinity zones.
    pairs = []
    for i in range(min(k, len(topics) - 1)):
        a, b = topics[i]["label"], topics[i + 1]["label"]
        pairs.append(f"{a} {b}")
    return pairs
