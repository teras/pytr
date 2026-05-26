# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""YouTube channel RSS adapter.

No auth, no quota, no telemetry: the cleanest possible source.
Fortress-safe.
"""
from __future__ import annotations

import logging
import re
import time
from xml.etree import ElementTree as ET

from .. import candidates
from ..egress import fetch, EgressBlocked
from .base import Source

log = logging.getLogger(__name__)

ATOM_NS = "{http://www.w3.org/2005/Atom}"
YT_NS = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA_NS = "{http://search.yahoo.com/mrss/}"

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"


class ChannelRSSSource(Source):
    name = "channel_rss"
    purpose = "channel_rss"

    async def fetch_channel(self, channel_id: str, *, mode: str) -> int:
        if not channel_id or self.in_backoff():
            return 0
        try:
            r = await fetch(FEED_URL.format(cid=channel_id), purpose=self.purpose, mode=mode)
            if r.status_code != 200:
                return 0
            xml = r.text
        except EgressBlocked:
            return 0
        except Exception as e:
            self.record_failure(str(e))
            return 0
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return 0
        count = 0
        channel_name = (root.findtext(f"{ATOM_NS}title") or "").strip()
        for entry in root.findall(f"{ATOM_NS}entry"):
            video_id = entry.findtext(f"{YT_NS}videoId") or ""
            title = (entry.findtext(f"{ATOM_NS}title") or "").strip()
            published = entry.findtext(f"{ATOM_NS}published") or ""
            try:
                pub_ts = int(time.mktime(time.strptime(published[:19], "%Y-%m-%dT%H:%M:%S")))
            except Exception:
                pub_ts = None
            thumb_el = entry.find(f"{MEDIA_NS}group/{MEDIA_NS}thumbnail")
            thumb_url = thumb_el.get("url") if thumb_el is not None else None
            if not video_id:
                continue
            candidates.upsert({
                "video_id": video_id,
                "title": title,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "published_at": pub_ts,
                "thumbnail_url": thumb_url,
            }, source=self.name, source_meta={"channel_id": channel_id})
            count += 1
        self.record_success()
        return count


def extract_channel_id_from_url(url: str) -> str | None:
    m = re.search(r"/channel/(UC[\w-]+)", url or "")
    return m.group(1) if m else None
