"""enhance-list contract: pass-through, spam filter, rerank skip when no persona."""
from __future__ import annotations

import pytest

from app import profile_sync
from app.db import init_db
from app.feeds import enhance


@pytest.fixture(scope="module", autouse=True)
def _init():
    init_db()
    profile_sync.ensure_taste_profile("uuid-enh")


@pytest.mark.asyncio
async def test_passthrough_for_unconfigured_surface():
    baseline = [{"video_id": "a", "title": "x"}]
    out = await enhance.enhance("uuid-enh", "playlist", {}, baseline)
    assert out["videos"] == baseline
    assert out["removed"] == []
    assert out["decorations"] == {}


@pytest.mark.asyncio
async def test_spam_filter_removes_shouty_titles():
    baseline = [
        {"video_id": "good", "title": "Calm video about jazz", "channel_id": "x"},
        {"video_id": "spam", "title": "YOU WILL NEVER BELIEVE THIS 🔥🔥🔥", "channel_id": "y"},
    ]
    out = await enhance.enhance("uuid-enh", "related", {}, baseline)
    ids = {v["video_id"] for v in out["videos"]}
    assert "good" in ids
    assert "spam" not in ids
    assert any(r["video_id"] == "spam" for r in out["removed"])


@pytest.mark.asyncio
async def test_search_keeps_all_when_no_persona_or_embedding():
    baseline = [{"video_id": str(i), "title": f"t{i}"} for i in range(5)]
    out = await enhance.enhance("uuid-enh", "search", {}, baseline)
    # No persona yet → query vec empty → returns baseline order with maybe empty decorations.
    assert {v["video_id"] for v in out["videos"]} == {"0", "1", "2", "3", "4"}
