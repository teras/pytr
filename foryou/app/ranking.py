# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vector math + MMR + channel quota — shared by all feeds."""
from __future__ import annotations

import math
from typing import Sequence


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def centroid(vectors: list[list[float]], weights: list[float] | None = None) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    out = [0.0] * dim
    weights = weights or [1.0] * len(vectors)
    total_w = sum(weights) or 1.0
    for v, w in zip(vectors, weights):
        if len(v) != dim:
            continue
        for i, x in enumerate(v):
            out[i] += x * w
    return [x / total_w for x in out]


def mmr(
    candidates: list[dict],
    query_vec: list[float],
    *,
    lam: float = 0.7,
    k: int = 50,
    channel_quota: int | None = 2,
    embedding_key: str = "embedding",
    channel_key: str = "channel_id",
) -> list[dict]:
    """Maximal Marginal Relevance with optional channel quota.

    Candidate dicts must carry an ``embedding`` list-of-floats. Missing
    embeddings are scored as 0 (sit at the bottom unless lifted by lam<0.5).
    """
    pool = list(candidates)
    selected: list[dict] = []
    channel_counts: dict[str, int] = {}

    def rel(item: dict) -> float:
        emb = item.get(embedding_key) or []
        return cosine(emb, query_vec)

    def diversity_penalty(item: dict) -> float:
        emb = item.get(embedding_key) or []
        if not selected:
            return 0.0
        return max(cosine(emb, s.get(embedding_key) or []) for s in selected)

    while pool and len(selected) < k:
        best = None
        best_score = -1e9
        for item in pool:
            ch = item.get(channel_key) or ""
            if channel_quota is not None and ch and channel_counts.get(ch, 0) >= channel_quota:
                continue
            score = lam * rel(item) - (1 - lam) * diversity_penalty(item)
            if score > best_score:
                best_score = score
                best = item
        if best is None:
            break
        selected.append(best)
        ch = best.get(channel_key) or ""
        if ch:
            channel_counts[ch] = channel_counts.get(ch, 0) + 1
        pool.remove(best)
    return selected


def stratified_sample(buckets: dict[str, list[dict]], k: int) -> list[dict]:
    """Round-robin pick from each bucket until we reach k items."""
    out: list[dict] = []
    if not buckets:
        return out
    keys = list(buckets.keys())
    indices = {k_: 0 for k_ in keys}
    while len(out) < k:
        progressed = False
        for k_ in keys:
            lst = buckets.get(k_, [])
            i = indices[k_]
            if i < len(lst):
                out.append(lst[i])
                indices[k_] = i + 1
                progressed = True
                if len(out) >= k:
                    return out
        if not progressed:
            break
    return out


def exploration_floor(richness: float, base: float = 0.25, cold_boost: float = 0.7) -> float:
    """Fraction of slots reserved for exploration, never below 25%.

    Cold profiles start near 70 %, decay linearly to the 25 % floor as richness
    rises to 1.0.
    """
    return max(base, cold_boost - (cold_boost - base) * richness)
