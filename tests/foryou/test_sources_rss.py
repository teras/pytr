"""RSS XML parsing — feed the adapter a fixed string and check candidate upserts."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import candidates
from app.db import init_db
from app.sources.rss import ChannelRSSSource


SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/">
  <title>My Channel</title>
  <entry>
    <yt:videoId>aaaaaaaaaaa</yt:videoId>
    <title>Hello video</title>
    <published>2026-04-01T12:00:00+00:00</published>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg" width="480" height="360"/>
    </media:group>
  </entry>
  <entry>
    <yt:videoId>bbbbbbbbbbb</yt:videoId>
    <title>Second video</title>
    <published>2026-04-02T13:00:00+00:00</published>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/bbbbbbbbbbb/hqdefault.jpg"/>
    </media:group>
  </entry>
</feed>
"""


@pytest.fixture(scope="module", autouse=True)
def _init():
    init_db()


@pytest.mark.asyncio
async def test_rss_parses_and_upserts():
    src = ChannelRSSSource()
    fake = MagicMock()
    fake.status_code = 200
    fake.text = SAMPLE_FEED
    with patch("app.sources.rss.fetch", AsyncMock(return_value=fake)):
        n = await src.fetch_channel("UCabc123", mode="fortress")
    assert n == 2
    got = candidates.fetch_many(["aaaaaaaaaaa", "bbbbbbbbbbb"])
    assert got["aaaaaaaaaaa"]["title"] == "Hello video"
    assert got["aaaaaaaaaaa"]["channel_id"] == "UCabc123"
