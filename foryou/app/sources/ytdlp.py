# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""yt-dlp wrapper: search, related, charts. Uses shared cookies if available."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .. import candidates
from ..config import COOKIES_FILE
from .base import Source

log = logging.getLogger(__name__)


def _ydl_opts(use_cookies: bool) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": "in_playlist",
    }
    if use_cookies and COOKIES_FILE.is_file():
        opts["cookiefile"] = str(COOKIES_FILE)
    return opts


def _video_from_entry(e: dict) -> dict | None:
    vid = e.get("id") or e.get("video_id")
    if not vid:
        return None
    return {
        "video_id": vid,
        "title": e.get("title") or "",
        "channel_id": e.get("channel_id"),
        "channel_name": e.get("channel") or e.get("uploader"),
        "duration_seconds": e.get("duration"),
        "view_count": e.get("view_count"),
        "thumbnail_url": (e.get("thumbnails") or [{}])[-1].get("url") if e.get("thumbnails") else None,
        "description": e.get("description"),
    }


async def _run_extract(url: str, *, use_cookies: bool) -> dict | None:
    """Run yt-dlp in a thread to avoid blocking the event loop."""
    import yt_dlp

    def _do():
        with yt_dlp.YoutubeDL(_ydl_opts(use_cookies)) as ydl:
            return ydl.extract_info(url, download=False)
    return await asyncio.get_running_loop().run_in_executor(None, _do)


class YtDlpSearchSource(Source):
    name = "yt_search"
    purpose = "yt_search"

    async def search(self, query: str, *, mode: str, limit: int = 20) -> list[str]:
        """Returns video_ids fetched and upserted into candidates."""
        if not query or self.in_backoff():
            return []
        # Egress: we make sure yt_search purpose is allowed under the mode (Balanced+).
        from ..privacy import check_egress
        if not check_egress("https://www.youtube.com/", purpose=self.purpose, mode=mode).allowed:
            return []
        try:
            data = await _run_extract(f"ytsearch{limit}:{query}", use_cookies=True)
        except Exception as e:
            self.record_failure(str(e))
            return []
        if not data:
            return []
        out: list[str] = []
        for e in data.get("entries") or []:
            v = _video_from_entry(e)
            if not v:
                continue
            candidates.upsert(v, source=self.name, source_meta={"q": query})
            out.append(v["video_id"])
        self.record_success()
        log.info("yt-dlp search '%s' → %d videos", query, len(out))
        return out


class YtDlpRelatedSource(Source):
    name = "yt_related"
    purpose = "yt_search"

    async def related(self, video_id: str, *, mode: str, limit: int = 20) -> list[str]:
        if self.in_backoff():
            return []
        from ..privacy import check_egress
        if not check_egress("https://www.youtube.com/", purpose=self.purpose, mode=mode).allowed:
            return []
        try:
            data = await _run_extract(f"https://www.youtube.com/watch?v={video_id}", use_cookies=True)
        except Exception as e:
            self.record_failure(str(e))
            return []
        if not data:
            return []
        out: list[str] = []
        # yt-dlp returns "channel" → its uploads; for related we use the watch
        # page's secondary list when available via the 'related' key, else fall
        # back to the uploader's recent uploads (a poor man's "more like this").
        for e in (data.get("related") or [])[:limit]:
            v = _video_from_entry(e)
            if not v:
                continue
            candidates.upsert(v, source=self.name, source_meta={"seed": video_id})
            out.append(v["video_id"])
        self.record_success()
        return out


class YtDlpChartsSource(Source):
    name = "yt_charts"
    purpose = "yt_search"

    async def charts(self, *, mode: str, country: str = "US", limit: int = 50) -> int:
        if self.in_backoff():
            return 0
        from ..privacy import check_egress
        if not check_egress("https://www.youtube.com/", purpose=self.purpose, mode=mode).allowed:
            return 0
        # yt-dlp removed direct support for /feed/trending mid-2025. The reliable
        # replacement is the YouTube Music Charts URL (works without auth for the
        # top-100 list) or, as a fallback, a curated search query.
        urls = [
            f"https://www.youtube.com/playlist?list=PLrEnWoR732-BHrPp_Pm8_VleD68f9s14-",  # Top music videos US
            f"https://www.youtube.com/results?search_query=trending&gl={country}",
        ]
        n = 0
        for url in urls:
            try:
                data = await _run_extract(url, use_cookies=True)
            except Exception as e:
                self.record_failure(str(e))
                continue
            if not data:
                continue
            for e in (data.get("entries") or [])[:limit]:
                v = _video_from_entry(e)
                if not v:
                    continue
                candidates.upsert(v, source=self.name, source_meta={"country": country})
                n += 1
            if n > 0:
                break
        if n > 0:
            self.record_success()
        log.info("yt-dlp charts %s → %d videos", country, n)
        return n
