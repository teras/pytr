# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite-backed profiles: preferences, watch history, favorites."""
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
DB_PATH = _DATA_DIR / "profiles.db"

_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    avatar_color TEXT NOT NULL DEFAULT '#cc0000',
    avatar_emoji TEXT NOT NULL DEFAULT '',
    pin TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    preferred_quality INTEGER NOT NULL DEFAULT 1080,
    subtitle_lang TEXT NOT NULL DEFAULT 'off',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS watch_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    thumbnail TEXT NOT NULL DEFAULT '',
    duration INTEGER NOT NULL DEFAULT 0,
    duration_str TEXT NOT NULL DEFAULT '',
    watched_at REAL NOT NULL,
    position REAL NOT NULL DEFAULT 0,
    UNIQUE(profile_id, video_id)
);

CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    thumbnail TEXT NOT NULL DEFAULT '',
    duration INTEGER NOT NULL DEFAULT 0,
    duration_str TEXT NOT NULL DEFAULT '',
    added_at REAL NOT NULL,
    UNIQUE(profile_id, video_id)
);

CREATE TABLE IF NOT EXISTS followed_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL,
    channel_name TEXT NOT NULL DEFAULT '',
    avatar_url TEXT NOT NULL DEFAULT '',
    added_at REAL NOT NULL,
    UNIQUE(profile_id, channel_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    created_at REAL NOT NULL,
    expiry REAL NOT NULL,
    last_ip TEXT
);
"""


def _connect() -> sqlite3.Connection:
    conn = getattr(_local, 'conn', None)
    if conn is not None:
        return conn
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    _local.conn = conn
    return conn


def init_db():
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        # Migration: add avatar_emoji if missing (existing DBs)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()]
        if "avatar_emoji" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN avatar_emoji TEXT NOT NULL DEFAULT ''")
        # Migration: add last_ip to sessions if missing
        sess_cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        if "last_ip" not in sess_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN last_ip TEXT")
        # Migration: add playlist/mix columns to favorites if missing
        fav_cols = [r[1] for r in conn.execute("PRAGMA table_info(favorites)").fetchall()]
        for col, typ, default in [
            ("item_type", "TEXT NOT NULL", "'video'"),
            ("playlist_id", "TEXT NOT NULL", "''"),
            ("first_video_id", "TEXT NOT NULL", "''"),
            ("video_count", "TEXT NOT NULL", "''"),
        ]:
            if col not in fav_cols:
                conn.execute(f"ALTER TABLE favorites ADD COLUMN {col} {typ} DEFAULT {default}")


def list_profiles() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, avatar_color, avatar_emoji, pin, is_admin FROM profiles ORDER BY id"
        ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "avatar_color": r["avatar_color"],
            "avatar_emoji": r["avatar_emoji"],
            "has_pin": r["pin"] is not None,
            "is_admin": bool(r["is_admin"]),
        }
        for r in rows
    ]


def get_profile(profile_id: int) -> dict | None:
    with _connect() as conn:
        r = conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    if not r:
        return None
    return {
        "id": r["id"],
        "name": r["name"],
        "avatar_color": r["avatar_color"],
        "avatar_emoji": r["avatar_emoji"],
        "has_pin": r["pin"] is not None,
        "is_admin": bool(r["is_admin"]),
        "preferred_quality": r["preferred_quality"],
        "subtitle_lang": r["subtitle_lang"],
    }


def create_profile(name: str, pin: str | None = None, avatar_color: str = "#cc0000",
                    avatar_emoji: str = "") -> dict:
    now = time.time()
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 30:
        raise ValueError("Name must be between 1 and 30 characters")
    clean_pin = pin if pin else None
    with _connect() as conn:
        # First profile becomes admin
        count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
        is_admin = 1 if count == 0 else 0
        cur = conn.execute(
            "INSERT INTO profiles (name, avatar_color, avatar_emoji, pin, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (clean_name, avatar_color, avatar_emoji, clean_pin, is_admin, now),
        )
    return {
        "id": cur.lastrowid,
        "name": clean_name,
        "avatar_color": avatar_color,
        "avatar_emoji": avatar_emoji,
        "has_pin": clean_pin is not None,
        "is_admin": bool(is_admin),
        "preferred_quality": 1080,
        "subtitle_lang": "off",
    }


def update_profile(profile_id: int, name: str | None = None,
                   avatar_color: str | None = None, avatar_emoji: str | None = None,
                   pin: str | None = None, pin_provided: bool = False) -> dict | None:
    """Update profile fields. Only non-None values are changed.
    For PIN: pin_provided=True means set/remove, False means don't touch."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if not row:
            return None

        new_name = row["name"]
        if name is not None:
            new_name = name.strip()
            if not new_name or len(new_name) > 30:
                raise ValueError("Name must be between 1 and 30 characters")

        new_pin = row["pin"]
        if pin_provided:
            new_pin = pin if pin else None

        conn.execute(
            "UPDATE profiles SET name=?, avatar_color=?, avatar_emoji=?, pin=? WHERE id=?",
            (new_name,
             avatar_color if avatar_color is not None else row["avatar_color"],
             avatar_emoji if avatar_emoji is not None else row["avatar_emoji"],
             new_pin, profile_id),
        )
        r = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()

    return {
        "id": r["id"],
        "name": r["name"],
        "avatar_color": r["avatar_color"],
        "avatar_emoji": r["avatar_emoji"],
        "has_pin": r["pin"] is not None,
        "is_admin": bool(r["is_admin"]),
        "preferred_quality": r["preferred_quality"],
        "subtitle_lang": r["subtitle_lang"],
    }



def delete_profile(profile_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    return cur.rowcount > 0


def verify_pin(profile_id: int, pin: str) -> bool:
    with _connect() as conn:
        r = conn.execute("SELECT pin FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    if not r:
        return False
    if r["pin"] is None:
        return True  # no PIN set
    return secrets.compare_digest(r["pin"], pin)



def update_preferences(profile_id: int, quality: int | None = None, subtitle_lang: str | None = None):
    with _connect() as conn:
        if quality is not None:
            conn.execute("UPDATE profiles SET preferred_quality = ? WHERE id = ?", (quality, profile_id))
        if subtitle_lang is not None:
            conn.execute("UPDATE profiles SET subtitle_lang = ? WHERE id = ?", (subtitle_lang, profile_id))


def save_position(profile_id: int, video_id: str, position: float,
                   title: str = "", channel: str = "", thumbnail: str = "",
                   duration: int = 0, duration_str: str = ""):
    now = time.time()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO watch_history (profile_id, video_id, title, channel, thumbnail, duration, duration_str, watched_at, position)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(profile_id, video_id) DO UPDATE SET
                   title = excluded.title,
                   channel = CASE WHEN excluded.channel = '' THEN watch_history.channel ELSE excluded.channel END,
                   thumbnail = CASE WHEN excluded.thumbnail = '' THEN watch_history.thumbnail ELSE excluded.thumbnail END,
                   duration = CASE WHEN excluded.duration = 0 THEN watch_history.duration ELSE excluded.duration END,
                   duration_str = CASE WHEN excluded.duration_str = '' THEN watch_history.duration_str ELSE excluded.duration_str END,
                   watched_at = excluded.watched_at,
                   position = excluded.position""",
            (profile_id, video_id, title, channel, thumbnail, duration, duration_str, now, position),
        )


def get_position(profile_id: int, video_id: str) -> float | None:
    with _connect() as conn:
        r = conn.execute(
            "SELECT position FROM watch_history WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        ).fetchone()
    return r["position"] if r else None


def get_watch_history(profile_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT video_id, title, channel, thumbnail, duration, duration_str, watched_at, position "
            "FROM watch_history WHERE profile_id = ? ORDER BY watched_at DESC LIMIT ? OFFSET ?",
            (profile_id, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_watch_history(profile_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM watch_history WHERE profile_id = ?", (profile_id,))


def delete_history_entry(profile_id: int, video_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM watch_history WHERE profile_id = ? AND video_id = ?", (profile_id, video_id))


def clear_favorites(profile_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM favorites WHERE profile_id = ?", (profile_id,))


def add_favorite(profile_id: int, video_id: str, title: str = "",
                 channel: str = "", thumbnail: str = "",
                 duration: int = 0, duration_str: str = "",
                 item_type: str = "video", playlist_id: str = "",
                 first_video_id: str = "", video_count: str = ""):
    now = time.time()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO favorites (profile_id, video_id, title, channel, thumbnail, duration, duration_str, added_at,
                   item_type, playlist_id, first_video_id, video_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(profile_id, video_id) DO UPDATE SET
                   title = excluded.title,
                   channel = CASE WHEN excluded.channel = '' THEN favorites.channel ELSE excluded.channel END,
                   thumbnail = CASE WHEN excluded.thumbnail = '' THEN favorites.thumbnail ELSE excluded.thumbnail END,
                   duration = CASE WHEN excluded.duration = 0 THEN favorites.duration ELSE excluded.duration END,
                   duration_str = CASE WHEN excluded.duration_str = '' THEN favorites.duration_str ELSE excluded.duration_str END,
                   item_type = excluded.item_type,
                   playlist_id = excluded.playlist_id,
                   first_video_id = CASE WHEN excluded.first_video_id = '' THEN favorites.first_video_id ELSE excluded.first_video_id END,
                   video_count = CASE WHEN excluded.video_count = '' THEN favorites.video_count ELSE excluded.video_count END,
                   added_at = excluded.added_at""",
            (profile_id, video_id, title, channel, thumbnail, duration, duration_str, now,
             item_type, playlist_id, first_video_id, video_count),
        )


def remove_favorite(profile_id: int, video_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM favorites WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        )
    return cur.rowcount > 0


def is_favorite(profile_id: int, video_id: str) -> bool:
    with _connect() as conn:
        r = conn.execute(
            "SELECT 1 FROM favorites WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        ).fetchone()
    return r is not None


def get_favorites(profile_id: int, limit: int = 50, offset: int = 0,
                   item_type: str | None = None) -> list[dict]:
    with _connect() as conn:
        if item_type:
            rows = conn.execute(
                "SELECT video_id, title, channel, thumbnail, duration, duration_str, added_at, "
                "item_type, playlist_id, first_video_id, video_count "
                "FROM favorites WHERE profile_id = ? AND item_type = ? ORDER BY added_at DESC LIMIT ? OFFSET ?",
                (profile_id, item_type, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT video_id, title, channel, thumbnail, duration, duration_str, added_at, "
                "item_type, playlist_id, first_video_id, video_count "
                "FROM favorites WHERE profile_id = ? ORDER BY added_at DESC LIMIT ? OFFSET ?",
                (profile_id, limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


# ── Followed Channels ────────────────────────────────────────────────────────

def follow_channel(profile_id: int, channel_id: str, channel_name: str = "", avatar_url: str = ""):
    now = time.time()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO followed_channels (profile_id, channel_id, channel_name, avatar_url, added_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(profile_id, channel_id) DO UPDATE SET
                   channel_name = CASE WHEN excluded.channel_name = '' THEN followed_channels.channel_name ELSE excluded.channel_name END,
                   avatar_url = CASE WHEN excluded.avatar_url = '' THEN followed_channels.avatar_url ELSE excluded.avatar_url END,
                   added_at = excluded.added_at""",
            (profile_id, channel_id, channel_name, avatar_url, now),
        )


def unfollow_channel(profile_id: int, channel_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM followed_channels WHERE profile_id = ? AND channel_id = ?",
            (profile_id, channel_id),
        )
    return cur.rowcount > 0


def is_following(profile_id: int, channel_id: str) -> bool:
    with _connect() as conn:
        r = conn.execute(
            "SELECT 1 FROM followed_channels WHERE profile_id = ? AND channel_id = ?",
            (profile_id, channel_id),
        ).fetchone()
    return r is not None


def get_followed_channels(profile_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT channel_id, channel_name, avatar_url, added_at "
            "FROM followed_channels WHERE profile_id = ? ORDER BY added_at DESC",
            (profile_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_followed_channels(profile_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM followed_channels WHERE profile_id = ?", (profile_id,))


# ── Settings ────────────────────────────────────────────────────────────────

def get_setting(key: str) -> str | None:
    with _connect() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else None


def set_setting(key: str, value: str | None):
    with _connect() as conn:
        if value is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def get_app_password() -> str | None:
    return get_setting("app_password")


def set_app_password(password: str):
    # Stored as plaintext: design choice for a self-hosted app where the DB
    # is only accessible to the server operator (who can reset it anyway).
    if not password or len(password) < 4:
        raise ValueError("Password must be at least 4 characters")
    set_setting("app_password", password)


# ── Long-term cleanup ───────────────────────────────────────────────────────

def cleanup_old_history(max_age_days: int = 90):
    """Delete watch history entries older than max_age_days."""
    cutoff = time.time() - max_age_days * 86400
    with _connect() as conn:
        cur = conn.execute("DELETE FROM watch_history WHERE watched_at < ?", (cutoff,))
        if cur.rowcount:
            log.info(f"Cleaned {cur.rowcount} watch history entries older than {max_age_days} days")


# ── Sessions (persistent) ─────────────────────────────────────────────────

_SESSION_EXPIRY = 10 * 365 * 86400  # 10 years


def create_session() -> tuple[str, dict]:
    token = secrets.token_urlsafe(32)
    now = time.time()
    expiry = now + _SESSION_EXPIRY
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, profile_id, created_at, expiry) VALUES (?, NULL, ?, ?)",
            (token, now, expiry),
        )
    return token, {"expiry": expiry, "profile_id": None}


def get_session(token: str) -> dict | None:
    with _connect() as conn:
        r = conn.execute(
            "SELECT token, profile_id, expiry FROM sessions WHERE token = ?", (token,)
        ).fetchone()
    if not r:
        return None
    if r["expiry"] < time.time():
        delete_session(token)
        return None
    return {"expiry": r["expiry"], "profile_id": r["profile_id"]}


def set_session_profile(token: str, profile_id: int | None):
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET profile_id = ? WHERE token = ?", (profile_id, token)
        )


def delete_session(token: str):
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def clear_profile_from_sessions(profile_id: int):
    """Clear profile_id from all sessions that have it (e.g. when profile is deleted)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET profile_id = NULL WHERE profile_id = ?", (profile_id,)
        )


def update_session_ip(token: str, ip: str):
    """Update the last_ip for a session."""
    with _connect() as conn:
        conn.execute("UPDATE sessions SET last_ip = ? WHERE token = ?", (ip, token))


def has_session_with_ip(ip: str) -> bool:
    """Check if any non-expired session has this IP."""
    now = time.time()
    with _connect() as conn:
        r = conn.execute(
            "SELECT 1 FROM sessions WHERE last_ip = ? AND expiry > ? LIMIT 1",
            (ip, now),
        ).fetchone()
    return r is not None


def cleanup_expired_sessions():
    now = time.time()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE expiry < ?", (now,))
        if cur.rowcount:
            log.info(f"Cleaned {cur.rowcount} expired sessions")


def _register_long_cleanup():
    try:
        from helpers import register_long_cleanup
        register_long_cleanup(cleanup_old_history)
        register_long_cleanup(cleanup_expired_sessions)
    except ImportError:
        pass


_register_long_cleanup()
