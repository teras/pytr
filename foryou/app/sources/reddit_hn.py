# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Community-curated YouTube link feeds: Reddit subreddits + HN Algolia."""
from __future__ import annotations

import logging
import re

from .. import candidates
from ..egress import fetch, EgressBlocked
from .base import Source

log = logging.getLogger(__name__)

YT_URL_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})")

DEFAULT_SUBREDDITS = ["videos", "documentaries", "mealtimevideos", "fullmoviesonyoutube"]


class RedditSource(Source):
    name = "reddit"
    purpose = "community_picks"

    async def fetch(self, *, mode: str, subreddits: list[str] | None = None, limit: int = 25) -> int:
        if self.in_backoff():
            return 0
        subs = subreddits or DEFAULT_SUBREDDITS
        total = 0
        for sub in subs:
            url = f"https://www.reddit.com/r/{sub}/top.json?t=week&limit={limit}"
            try:
                r = await fetch(url, purpose=self.purpose, mode=mode,
                                headers={"User-Agent": "PYTR-ForYou/1.0"})
                if r.status_code != 200:
                    continue
                data = r.json()
            except EgressBlocked:
                return 0
            except Exception as e:
                self.record_failure(str(e))
                continue
            for ch in (data.get("data") or {}).get("children") or []:
                d = ch.get("data") or {}
                ext = d.get("url_overridden_by_dest") or d.get("url") or ""
                m = YT_URL_RE.search(ext)
                if not m:
                    continue
                vid = m.group(1)
                candidates.upsert({
                    "video_id": vid,
                    "title": d.get("title") or "",
                    "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                }, source=self.name, source_meta={"sub": sub, "score": d.get("score", 0)})
                total += 1
        self.record_success()
        log.info("reddit community fetch → %d videos", total)
        return total


class HNAlgoliaSource(Source):
    name = "hn_algolia"
    purpose = "community_picks"

    async def fetch(self, *, mode: str, limit: int = 25) -> int:
        if self.in_backoff():
            return 0
        url = f"https://hn.algolia.com/api/v1/search?query=youtube.com&tags=story&hitsPerPage={limit}"
        try:
            r = await fetch(url, purpose=self.purpose, mode=mode)
            if r.status_code != 200:
                return 0
            data = r.json()
        except EgressBlocked:
            return 0
        except Exception as e:
            self.record_failure(str(e))
            return 0
        n = 0
        for h in data.get("hits") or []:
            link = h.get("url") or ""
            m = YT_URL_RE.search(link)
            if not m:
                continue
            vid = m.group(1)
            candidates.upsert({
                "video_id": vid,
                "title": h.get("title") or "",
                "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            }, source=self.name, source_meta={"hn_points": h.get("points", 0)})
            n += 1
        self.record_success()
        log.info("HN community fetch → %d videos", n)
        return n
