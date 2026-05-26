# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Egress-gated HTTP client. All outbound calls in foryou go through here."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from . import privacy
from .config import PYTR_INTERNAL_URL

log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    return _client


async def close_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class EgressBlocked(Exception):
    """Raised when the egress gate refuses an outbound call."""


async def fetch(url: str, *, purpose: str, mode: str,
                profile_uuid: str | None = None,
                method: str = "GET",
                **kwargs: Any) -> httpx.Response:
    """Perform an HTTP request only if (url, purpose) is allowed in mode."""
    # Calls to the PYTR sibling container are *not* egress — they live on the
    # same host and may freely share data per the threat model.
    if url.startswith(PYTR_INTERNAL_URL):
        return await get_client().request(method, url, **kwargs)
    dec = privacy.check_egress(url, purpose, mode)
    if not dec.allowed:
        log.info("egress blocked: %s purpose=%s mode=%s reason=%s", dec.host, dec.purpose, mode, dec.reason)
        raise EgressBlocked(f"{dec.host} blocked: {dec.reason}")
    # Cloud-tier purposes get audited.
    if purpose == "llm":
        privacy.audit(profile_uuid, dec.host, purpose, kwargs.get("audit_summary", ""))
    kwargs.pop("audit_summary", None)
    return await get_client().request(method, url, **kwargs)
