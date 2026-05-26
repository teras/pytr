# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Enrichment adapters: Wikidata, MusicBrainz, Last.fm.

All write to a single shared enrichment_cache table. Failures are quiet
(see source.base) so a single broken source doesn't surface as an error.
"""
from __future__ import annotations

import json
import logging
import time

from ..db import cursor
from ..egress import fetch, EgressBlocked
from ..sources.base import Source

log = logging.getLogger(__name__)

_CACHE_TTL_DAYS = 30


def _cache_get(entity_type: str, entity_id: str, source: str) -> dict | None:
    cutoff = int(time.time()) - _CACHE_TTL_DAYS * 86400
    with cursor() as c:
        row = c.execute(
            "SELECT payload, fetched_at FROM enrichment_cache "
            "WHERE entity_type=? AND entity_id=? AND source=? AND fetched_at > ?",
            (entity_type, entity_id, source, cutoff),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload"])
    except Exception:
        return None


def _cache_set(entity_type: str, entity_id: str, source: str, payload: dict):
    with cursor(write=True) as c:
        c.execute(
            "INSERT OR REPLACE INTO enrichment_cache "
            "(entity_type, entity_id, source, payload, fetched_at) VALUES (?, ?, ?, ?, ?)",
            (entity_type, entity_id, source, json.dumps(payload), int(time.time())),
        )


class WikidataAdapter(Source):
    name = "wikidata"
    purpose = "wikidata"

    async def for_channel(self, channel_id: str, *, mode: str) -> dict | None:
        if not channel_id or self.in_backoff():
            return None
        cached = _cache_get("channel", channel_id, self.name)
        if cached is not None:
            return cached
        query = (
            f'SELECT ?item ?itemLabel ?occupationLabel ?genreLabel WHERE {{ '
            f'?item wdt:P2397 "{channel_id}" . '
            f'OPTIONAL {{ ?item wdt:P106 ?occupation . }} '
            f'OPTIONAL {{ ?item wdt:P136 ?genre . }} '
            f'SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }} '
            f'}} LIMIT 10'
        )
        try:
            r = await fetch("https://query.wikidata.org/sparql",
                            purpose=self.purpose, mode=mode,
                            params={"query": query, "format": "json"},
                            headers={"User-Agent": "PYTR-ForYou/1.0"})
            if r.status_code != 200:
                return None
            data = r.json()
        except EgressBlocked:
            return None
        except Exception as e:
            self.record_failure(str(e))
            return None
        bindings = data.get("results", {}).get("bindings", [])
        if not bindings:
            payload = {"empty": True}
        else:
            first = bindings[0]
            payload = {
                "item": (first.get("item") or {}).get("value"),
                "label": (first.get("itemLabel") or {}).get("value"),
                "occupations": list({(b.get("occupationLabel") or {}).get("value") for b in bindings if b.get("occupationLabel")}),
                "genres": list({(b.get("genreLabel") or {}).get("value") for b in bindings if b.get("genreLabel")}),
            }
        _cache_set("channel", channel_id, self.name, payload)
        self.record_success()
        return payload


class MusicBrainzAdapter(Source):
    name = "musicbrainz"
    purpose = "musicbrainz"

    async def by_artist(self, artist_name: str, *, mode: str) -> dict | None:
        if not artist_name or self.in_backoff():
            return None
        cached = _cache_get("artist", artist_name.lower(), self.name)
        if cached is not None:
            return cached
        try:
            r = await fetch("https://musicbrainz.org/ws/2/artist/",
                            purpose=self.purpose, mode=mode,
                            params={"query": artist_name, "fmt": "json", "limit": 1},
                            headers={"User-Agent": "PYTR-ForYou/1.0"})
            if r.status_code != 200:
                return None
            data = r.json()
        except EgressBlocked:
            return None
        except Exception as e:
            self.record_failure(str(e))
            return None
        artists = data.get("artists") or []
        if not artists:
            payload = {"empty": True}
        else:
            a = artists[0]
            payload = {
                "id": a.get("id"),
                "name": a.get("name"),
                "country": a.get("country"),
                "tags": [t.get("name") for t in a.get("tags") or []],
                "type": a.get("type"),
            }
        _cache_set("artist", artist_name.lower(), self.name, payload)
        self.record_success()
        return payload


class LastFmAdapter(Source):
    name = "lastfm"
    purpose = "lastfm"

    def __init__(self, api_key: str = ""):
        super().__init__()
        self.api_key = api_key

    async def similar_artists(self, artist_name: str, *, mode: str) -> dict | None:
        if not artist_name or not self.api_key or self.in_backoff():
            return None
        cached = _cache_get("similar_artists", artist_name.lower(), self.name)
        if cached is not None:
            return cached
        try:
            r = await fetch("https://ws.audioscrobbler.com/2.0/",
                            purpose=self.purpose, mode=mode,
                            params={"method": "artist.getsimilar", "artist": artist_name,
                                    "api_key": self.api_key, "format": "json", "limit": 10})
            if r.status_code != 200:
                return None
            data = r.json()
        except EgressBlocked:
            return None
        except Exception as e:
            self.record_failure(str(e))
            return None
        sim = (data.get("similarartists") or {}).get("artist") or []
        payload = {"similar": [{"name": a.get("name"), "match": a.get("match")} for a in sim]}
        _cache_set("similar_artists", artist_name.lower(), self.name, payload)
        self.record_success()
        return payload
