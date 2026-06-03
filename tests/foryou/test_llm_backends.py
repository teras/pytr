"""LLM backend dispatch + hash embedding determinism."""
from __future__ import annotations

import pytest

from app import config
from app.llm import backend as bk


@pytest.fixture(autouse=True)
def _reset():
    bk.reset_backends_for_tests()
    yield
    bk.reset_backends_for_tests()


def test_none_backend_when_configured():
    config.LLM_BACKEND = "none"
    b = bk.get_llm_backend()
    assert b.name == "none"


def test_unknown_backend_falls_back_to_none():
    config.LLM_BACKEND = "bogus"
    b = bk.get_llm_backend()
    assert b.name == "none"


def test_openai_backend_dispatch():
    config.LLM_BACKEND = "openai"
    config.EMBED_BACKEND = "openai"
    bk.reset_backends_for_tests()
    assert bk.get_llm_backend().name == "openai"
    assert bk.get_embedding_backend().name == "openai"


async def test_hash_embedding_deterministic():
    config.EMBED_BACKEND = "hash"
    bk.reset_backends_for_tests()
    emb = bk.get_embedding_backend()
    a = await emb.embed(["hello world"])
    b = await emb.embed(["hello world"])
    assert a == b
    assert len(a[0]) == config.EMBED_DIM_HASH


async def test_hash_embedding_distinct_inputs_differ():
    config.EMBED_BACKEND = "hash"
    bk.reset_backends_for_tests()
    emb = bk.get_embedding_backend()
    a, b = await emb.embed(["hello world", "totally other thing"])
    from app.ranking import cosine
    assert cosine(a, b) < 0.95
