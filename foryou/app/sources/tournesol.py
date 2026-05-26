# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tournesol API adapter — community-curated YouTube quality rankings."""
from __future__ import annotations

import logging
import time

from .. import candidates
from ..egress import fetch, EgressBlocked
from .base import Source

log = logging.getLogger(__name__)

BASE = "https://api.tournesol.app/polls/videos/recommendations/"


class TournesolSource(Source):
    name = "tournesol"
    purpose = "tournesol"

    async def fetch_top(self, *, mode: str, language: str | None = None, limit: int = 50) -> int:
        """Pull top-rated videos from Tournesol.

        Tournesol is a French-origin project; with no language hint it returns
        a heavily French-biased mix. We default to English when the caller does
        not specify a language, and only switch when the user has explicitly
        picked one in their PYTR profile.
        """
        if self.in_backoff():
            return 0
        params = {"limit": limit, "unsafe": "false"}
        # The Tournesol API accepts a comma-separated language filter.
        if language and language != "auto":
            params["language"] = language
        else:
            params["language"] = "en"
        try:
            r = await fetch(BASE, purpose=self.purpose, mode=mode, params=params)
            r.raise_for_status()
            data = r.json()
            results = data.get("results", []) or []
        except EgressBlocked:
            return 0
        except Exception as e:
            self.record_failure(str(e))
            return 0
        count = 0
        for v in results:
            ent = v.get("entity") or {}
            metadata = ent.get("metadata") or {}
            rating = v.get("collective_rating") or {}
            rec_meta = v.get("recommendation_metadata") or {}
            video_id = metadata.get("video_id") or (ent.get("uid") or "").replace("yt:", "")
            if not video_id:
                continue
            published = metadata.get("publication_date")
            try:
                pub_ts = int(time.mktime(time.strptime(published[:19], "%Y-%m-%dT%H:%M:%S"))) if published else None
            except Exception:
                pub_ts = None
            candidates.upsert({
                "video_id": video_id,
                "title": metadata.get("name") or "",
                "channel_id": metadata.get("channel_id"),
                "channel_name": metadata.get("uploader"),
                "published_at": pub_ts,
                "duration_seconds": metadata.get("duration"),
                "view_count": metadata.get("views"),
                "description": metadata.get("description"),
                "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "quality_score": rating.get("tournesol_score") or rec_meta.get("total_score"),
            }, source=self.name)
            count += 1
        self.record_success()
        log.info("tournesol fetched %d candidates (lang=%s)", count, language)
        return count
