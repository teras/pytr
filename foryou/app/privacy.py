# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Privacy modes + egress allowlist gate.

Single chokepoint for all outbound HTTP. Refuses destinations not on the
active allowlist; sanitises payloads; writes audit rows for cloud calls.
"""
from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass

from .db import cursor

FORTRESS = "fortress"
BALANCED = "balanced"
CLOUD = "cloud"

# Set of (host_suffix, purpose) entries. We compare a destination URL's host
# against the suffixes. Suffixes are matched right-anchored so 'youtube.com'
# matches 'www.youtube.com' but not 'evilyoutube.com.evil.example'.
_ALLOWLISTS = {
    FORTRESS: {
        ("youtube.com", "channel_rss"),
        ("googlevideo.com", "rss"),
        ("api.tournesol.app", "tournesol"),
        ("sponsor.ajay.app", "sponsorblock"),
        ("query.wikidata.org", "wikidata"),
        ("dumps.wikimedia.org", "wikipedia_clickstream"),
        ("musicbrainz.org", "musicbrainz"),
        ("ws.audioscrobbler.com", "lastfm"),
        ("huggingface.co", "model_setup"),  # setup-time only; runtime egress to HF blocked
        ("cdn-lfs.huggingface.co", "model_setup"),
    },
    BALANCED: {
        # Everything Fortress allows, plus anonymous YT search and curated link feeds.
        ("youtube.com", "yt_search"),
        ("googleapis.com", "yt_search"),
        ("youtu.be", "yt_search"),
        ("reddit.com", "community_picks"),
        ("hn.algolia.com", "community_picks"),
    },
    CLOUD: {
        # Cloud-only: LLM providers.
        ("generativelanguage.googleapis.com", "llm"),
        ("api.anthropic.com", "llm"),
        ("api.openai.com", "llm"),
        ("api.groq.com", "llm"),
        ("api.deepseek.com", "llm"),
    },
}

# Fortress also allows HuggingFace at *setup time only*. Runtime calls are blocked
# unless this flag is flipped — kept here as a process-level guard.
_HF_SETUP_OPEN: bool = False


def open_hf_for_setup():
    global _HF_SETUP_OPEN
    _HF_SETUP_OPEN = True


def close_hf_for_setup():
    global _HF_SETUP_OPEN
    _HF_SETUP_OPEN = False


def effective_allowlist(mode: str) -> set[tuple[str, str]]:
    """Cumulative allowlist for the mode."""
    mode = (mode or BALANCED).lower()
    if mode == FORTRESS:
        out = set(_ALLOWLISTS[FORTRESS])
    elif mode == BALANCED:
        out = set(_ALLOWLISTS[FORTRESS]) | set(_ALLOWLISTS[BALANCED])
    elif mode == CLOUD:
        out = set(_ALLOWLISTS[FORTRESS]) | set(_ALLOWLISTS[BALANCED]) | set(_ALLOWLISTS[CLOUD])
    else:
        out = set(_ALLOWLISTS[BALANCED]) | set(_ALLOWLISTS[FORTRESS])
    # Strip HF unless currently open for setup.
    if not _HF_SETUP_OPEN:
        out = {e for e in out if not e[0].endswith("huggingface.co")}
    return out


@dataclass(frozen=True)
class EgressDecision:
    allowed: bool
    reason: str
    host: str
    purpose: str


def check_egress(url: str, purpose: str, mode: str) -> EgressDecision:
    """Decide whether the URL may be fetched under the privacy mode."""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return EgressDecision(False, "invalid_url", "", purpose)
    host = host.lower()
    if not host:
        return EgressDecision(False, "empty_host", "", purpose)
    allow = effective_allowlist(mode)
    for suffix, allowed_purpose in allow:
        if (host == suffix or host.endswith("." + suffix)) and allowed_purpose == purpose:
            return EgressDecision(True, "ok", host, purpose)
    # Looser host-only match (any purpose) for utility — used to give a clearer log message
    for suffix, _ in allow:
        if host == suffix or host.endswith("." + suffix):
            return EgressDecision(False, "purpose_mismatch", host, purpose)
    return EgressDecision(False, "host_not_allowed", host, purpose)


def audit(profile_uuid: str | None, destination: str, purpose: str, summary: str = ""):
    """Insert an audit row for outbound calls — primarily for Cloud-mode visibility."""
    try:
        with cursor(write=True) as c:
            c.execute(
                "INSERT OR REPLACE INTO outbound_audit (ts, profile_uuid, destination, purpose, payload_summary) VALUES (?, ?, ?, ?, ?)",
                (int(time.time()), profile_uuid, destination, purpose, summary[:512]),
            )
    except Exception:
        # never block a request over auditing
        pass


def sanitize_payload(payload: dict) -> dict:
    """Strip personal identifiers before sending anywhere outside the box."""
    blocked_keys = {"profile_uuid", "profile_id", "session_id", "user_id", "watch_history",
                    "favorites_list", "subscribed_channels", "ip", "client_ip"}
    out = {}
    for k, v in payload.items():
        if k in blocked_keys:
            continue
        if isinstance(v, dict):
            out[k] = sanitize_payload(v)
        elif isinstance(v, list):
            out[k] = [sanitize_payload(x) if isinstance(x, dict) else x for x in v]
        else:
            out[k] = v
    return out
