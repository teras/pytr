"""SQLite-backed profiles: preferences, watch history, favorites."""
import sqlite3
import threading
import time
from pathlib import Path

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

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
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
    with _connect() as conn:
        # First profile becomes admin
        count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
        is_admin = 1 if count == 0 else 0
        cur = conn.execute(
            "INSERT INTO profiles (name, avatar_color, avatar_emoji, pin, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name.strip(), avatar_color, avatar_emoji, pin if pin else None, is_admin, now),
        )
        profile_id = cur.lastrowid
    return get_profile(profile_id)


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
    return r["pin"] == pin


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


def add_favorite(profile_id: int, video_id: str, title: str = "",
                 channel: str = "", thumbnail: str = "",
                 duration: int = 0, duration_str: str = ""):
    now = time.time()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO favorites (profile_id, video_id, title, channel, thumbnail, duration, duration_str, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(profile_id, video_id) DO UPDATE SET
                   title = excluded.title,
                   channel = CASE WHEN excluded.channel = '' THEN favorites.channel ELSE excluded.channel END,
                   thumbnail = CASE WHEN excluded.thumbnail = '' THEN favorites.thumbnail ELSE excluded.thumbnail END,
                   duration = CASE WHEN excluded.duration = 0 THEN favorites.duration ELSE excluded.duration END,
                   duration_str = CASE WHEN excluded.duration_str = '' THEN favorites.duration_str ELSE excluded.duration_str END,
                   added_at = excluded.added_at""",
            (profile_id, video_id, title, channel, thumbnail, duration, duration_str, now),
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


def get_favorites(profile_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT video_id, title, channel, thumbnail, duration, duration_str, added_at "
            "FROM favorites WHERE profile_id = ? ORDER BY added_at DESC LIMIT ? OFFSET ?",
            (profile_id, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


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


def set_app_password(password: str | None):
    set_setting("app_password", password if password else None)
