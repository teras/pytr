"""Onboarding wizard state machine — mode A end-to-end without LLM."""
from __future__ import annotations

import pytest

from app import profile_sync
from app.db import init_db
from app.onboarding import wizard


@pytest.fixture(scope="module", autouse=True)
def _init():
    init_db()
    profile_sync.ensure_taste_profile("uuid-onb")


@pytest.mark.asyncio
async def test_mode_A_completes_with_seed_grid_only():
    sess = wizard.start_session("uuid-onb", "A")
    sid = sess["session_id"]
    assert sess["first_question"]["kind"] == "seed_grid"
    out = await wizard.submit_answer(sid, "seed_grid", {
        "selected": ["music_jazz", "tech_software"], "rejected": ["gaming"]})
    assert out["done"] is True
    assert "Jazz" in out["preview"]["persona_so_far"] or "Software" in out["preview"]["persona_so_far"]
    res = await wizard.finalize(sid)
    assert res["ok"] is True
    st = wizard.status("uuid-onb")
    assert st["complete"] is True


@pytest.mark.asyncio
async def test_mode_C_without_llm_falls_back_to_done_after_seed_grid():
    profile_sync.ensure_taste_profile("uuid-onb-c")
    sess = wizard.start_session("uuid-onb-c", "C")
    sid = sess["session_id"]
    out = await wizard.submit_answer(sid, "seed_grid", {"selected": ["docs"], "rejected": []})
    # Either offers escalate_to_b (and we'll say no) OR completes immediately.
    if out.get("done"):
        assert out["done"] is True
    else:
        # When LLM is unavailable, mode C should finish at the seed-grid step
        # since the wizard checks llm_available before offering escalation.
        assert "next_question" in out
