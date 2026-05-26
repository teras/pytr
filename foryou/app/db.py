# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite schema + connection management for foryou.db."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

from .config import DB_PATH

_local = threading.local()
_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    channel_id TEXT,
    channel_name TEXT,
    published_at INTEGER,
    duration_seconds INTEGER,
    view_count INTEGER,
    description TEXT,
    thumbnail_url TEXT,
    source TEXT NOT NULL,
    source_meta TEXT,
    embedding BLOB,
    fetched_at INTEGER NOT NULL,
    last_used_at INTEGER,
    quality_score REAL
);
CREATE INDEX IF NOT EXISTS idx_candidates_last_used ON candidates(last_used_at);
CREATE INDEX IF NOT EXISTS idx_candidates_published ON candidates(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_source ON candidates(source);

CREATE TABLE IF NOT EXISTS taste_profiles (
    profile_uuid TEXT PRIMARY KEY,
    buckets TEXT,
    centroid_embedding BLOB,
    persona_text TEXT,
    persona_embedding BLOB,
    persona_edited_by_user INTEGER DEFAULT 0,
    persona_source TEXT,
    signal_richness REAL NOT NULL DEFAULT 0.0,
    sparsity_state TEXT NOT NULL DEFAULT 'cold',
    onboarding_seed_interests TEXT,
    onboarding_completed_at INTEGER,
    session_centroids TEXT,
    time_of_day_profile TEXT,
    timezone TEXT,
    last_refreshed_at INTEGER NOT NULL DEFAULT 0,
    history_signature TEXT,
    privacy_mode TEXT NOT NULL DEFAULT 'balanced'
);

CREATE TABLE IF NOT EXISTS kg_nodes (
    profile_uuid TEXT NOT NULL,
    node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    label TEXT NOT NULL,
    language TEXT,
    affinity REAL NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    metadata TEXT,
    PRIMARY KEY (profile_uuid, node_id),
    FOREIGN KEY (profile_uuid) REFERENCES taste_profiles(profile_uuid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_affinity ON kg_nodes(profile_uuid, affinity DESC);

CREATE TABLE IF NOT EXISTS kg_edges (
    profile_uuid TEXT NOT NULL,
    from_node TEXT NOT NULL,
    to_node TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL,
    last_updated_at INTEGER NOT NULL,
    PRIMARY KEY (profile_uuid, from_node, to_node, edge_type),
    FOREIGN KEY (profile_uuid) REFERENCES taste_profiles(profile_uuid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS enrichment_cache (
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (entity_type, entity_id, source)
);

CREATE TABLE IF NOT EXISTS latent_signals (
    profile_uuid TEXT NOT NULL,
    video_id TEXT NOT NULL,
    completion_pct REAL,
    rewatch_count INTEGER DEFAULT 0,
    rewind_events INTEGER DEFAULT 0,
    fastforward_events INTEGER DEFAULT 0,
    time_of_day_bucket TEXT,
    day_of_week TEXT,
    session_id TEXT,
    last_watched_at INTEGER NOT NULL,
    PRIMARY KEY (profile_uuid, video_id),
    FOREIGN KEY (profile_uuid) REFERENCES taste_profiles(profile_uuid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_latent_session ON latent_signals(profile_uuid, session_id);

CREATE TABLE IF NOT EXISTS failed_searches (
    profile_uuid TEXT NOT NULL,
    query TEXT NOT NULL,
    last_ts INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (profile_uuid, query),
    FOREIGN KEY (profile_uuid) REFERENCES taste_profiles(profile_uuid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feed_defs (
    feed_id TEXT PRIMARY KEY,
    profile_uuid TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT,
    pinned_order INTEGER,
    config TEXT,
    FOREIGN KEY (profile_uuid) REFERENCES taste_profiles(profile_uuid) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_feed_pinned ON feed_defs(profile_uuid, pinned_order) WHERE pinned_order IS NOT NULL;

CREATE TABLE IF NOT EXISTS feed_items (
    feed_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    why TEXT,
    source_of_rec TEXT,
    generated_at INTEGER NOT NULL,
    PRIMARY KEY (feed_id, video_id),
    FOREIGN KEY (feed_id) REFERENCES feed_defs(feed_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feedback (
    profile_uuid TEXT NOT NULL,
    video_id TEXT NOT NULL,
    source_of_rec TEXT NOT NULL,
    signal TEXT NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY (profile_uuid, video_id, source_of_rec, signal),
    FOREIGN KEY (profile_uuid) REFERENCES taste_profiles(profile_uuid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS impressions_daily (
    profile_uuid TEXT NOT NULL,
    video_id TEXT NOT NULL,
    day INTEGER NOT NULL,
    feed_kind TEXT,
    shown_count INTEGER NOT NULL DEFAULT 1,
    clicked_count INTEGER NOT NULL DEFAULT 0,
    watched_30s_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile_uuid, video_id, day),
    FOREIGN KEY (profile_uuid) REFERENCES taste_profiles(profile_uuid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_impressions_recent ON impressions_daily(profile_uuid, day);

CREATE TABLE IF NOT EXISTS hit_rate_history (
    profile_uuid TEXT NOT NULL,
    period_start INTEGER NOT NULL,
    period_end INTEGER NOT NULL,
    shown INTEGER NOT NULL,
    clicked INTEGER NOT NULL,
    watched_30s INTEGER NOT NULL,
    PRIMARY KEY (profile_uuid, period_start),
    FOREIGN KEY (profile_uuid) REFERENCES taste_profiles(profile_uuid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shoulder_imports (
    profile_uuid TEXT NOT NULL,
    source TEXT NOT NULL,
    imported_at INTEGER NOT NULL,
    entity_count INTEGER,
    file_path TEXT NOT NULL,
    PRIMARY KEY (profile_uuid, source, imported_at),
    FOREIGN KEY (profile_uuid) REFERENCES taste_profiles(profile_uuid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS persona_history (
    profile_uuid TEXT NOT NULL,
    version INTEGER NOT NULL,
    persona_text TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (profile_uuid, version),
    FOREIGN KEY (profile_uuid) REFERENCES taste_profiles(profile_uuid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS outbound_audit (
    ts INTEGER NOT NULL,
    profile_uuid TEXT,
    destination TEXT NOT NULL,
    purpose TEXT NOT NULL,
    payload_summary TEXT,
    PRIMARY KEY (ts, destination, purpose)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS onboarding_sessions (
    session_id TEXT PRIMARY KEY,
    profile_uuid TEXT NOT NULL,
    mode TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    finalized_at INTEGER
);
"""


def _connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    _local.conn = conn
    return conn


def init_db():
    with _write_lock:
        conn = _connect()
        conn.executescript(SCHEMA)


@contextmanager
def cursor(write: bool = False):
    """Return a cursor; serialize writes via a process-wide lock."""
    if write:
        _write_lock.acquire()
    try:
        cur = _connect().cursor()
        try:
            yield cur
        finally:
            cur.close()
    finally:
        if write:
            _write_lock.release()
