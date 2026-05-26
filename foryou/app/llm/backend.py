# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""LLM + embedding backend dispatcher."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import struct
from dataclasses import dataclass
from typing import Any

import httpx

from .. import config
from ..egress import get_client

log = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    text: str
    backend: str
    model: str


class LLMBackend:
    """Abstract LLM backend. Subclasses implement ``generate`` / ``available``."""

    name = "abstract"
    model = ""

    async def available(self) -> bool:
        raise NotImplementedError

    async def generate(self, prompt: str, *,
                       system: str | None = None,
                       json_mode: bool = False,
                       max_tokens: int = 1024,
                       temperature: float = 0.4,
                       privacy_mode: str | None = None,
                       profile_uuid: str | None = None) -> LLMResponse:
        raise NotImplementedError


class EmbeddingBackend:
    name = "abstract"
    dim = 0

    async def available(self) -> bool:
        raise NotImplementedError

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


# ── None backend (no LLM features) ──────────────────────────────────────────

class NoneLLM(LLMBackend):
    name = "none"

    async def available(self) -> bool:
        return False

    async def generate(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
        raise RuntimeError("LLM backend disabled (FORYOU_LLM_BACKEND=none)")


# ── Ollama backend (BYO local) ───────────────────────────────────────────────

class OllamaLLM(LLMBackend):
    name = "ollama"

    def __init__(self):
        self.model = config.OLLAMA_MODEL
        self.url = config.OLLAMA_URL.rstrip("/")
        self._cached_available: bool | None = None
        self._available_ts = 0.0

    async def available(self) -> bool:
        # Cheap 5s cache — onboarding/feed paths probe this on every call.
        import time
        if self._cached_available is not None and (time.time() - self._available_ts) < 5:
            return self._cached_available
        try:
            r = await get_client().get(f"{self.url}/api/tags", timeout=2.0)
            ok = r.status_code == 200
        except Exception:
            ok = False
        self._cached_available = ok
        self._available_ts = time.time()
        return ok

    async def generate(self, prompt: str, *, system: str | None = None,
                       json_mode: bool = False, max_tokens: int = 1024,
                       temperature: float = 0.4, **_: Any) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system:
            body["system"] = system
        if json_mode:
            body["format"] = "json"
        r = await get_client().post(f"{self.url}/api/generate", json=body, timeout=120.0)
        r.raise_for_status()
        data = r.json()
        return LLMResponse(text=data.get("response", ""), backend=self.name, model=self.model)


class OllamaEmbedding(EmbeddingBackend):
    name = "ollama"

    def __init__(self):
        self.model = config.OLLAMA_EMBED_MODEL
        self.url = config.OLLAMA_URL.rstrip("/")
        self.dim = 768  # EmbeddingGemma default; lazily corrected after first call.

    async def available(self) -> bool:
        try:
            r = await get_client().get(f"{self.url}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            r = await get_client().post(
                f"{self.url}/api/embeddings",
                json={"model": self.model, "prompt": t},
                timeout=60.0,
            )
            r.raise_for_status()
            vec = r.json().get("embedding", [])
            out.append(vec)
            if vec and self.dim != len(vec):
                self.dim = len(vec)
        return out


# ── llama.cpp backend (bundled, optional) ───────────────────────────────────

class LlamaCppLLM(LLMBackend):
    name = "llamacpp"

    def __init__(self):
        self.model = config.LLAMACPP_MODEL_PATH
        self._llm = None

    async def available(self) -> bool:
        try:
            import llama_cpp  # noqa: F401
        except ImportError:
            return False
        from pathlib import Path
        return Path(self.model).is_file()

    def _lazy_load(self):
        if self._llm is not None:
            return
        from llama_cpp import Llama
        self._llm = Llama(model_path=self.model, n_ctx=8192, n_threads=4, verbose=False)

    async def generate(self, prompt: str, *, system: str | None = None,
                       json_mode: bool = False, max_tokens: int = 1024,
                       temperature: float = 0.4, **_: Any) -> LLMResponse:
        loop = asyncio.get_running_loop()

        def _do():
            self._lazy_load()
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            kwargs: dict[str, Any] = {"max_tokens": max_tokens, "temperature": temperature}
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            assert self._llm is not None
            out = self._llm.create_chat_completion(messages=messages, **kwargs)
            return out["choices"][0]["message"]["content"]

        text = await loop.run_in_executor(None, _do)
        return LLMResponse(text=text or "", backend=self.name, model=self.model)


# ── Cloud API backend ────────────────────────────────────────────────────────

class CloudAPILLM(LLMBackend):
    name = "api"

    def __init__(self):
        self.provider = config.API_PROVIDER
        self.api_key = config.API_KEY
        self.model = config.API_MODEL or self._default_model()

    def _default_model(self) -> str:
        return {
            "gemini": "gemini-2.5-flash",
            "anthropic": "claude-haiku-4-5-20251001",
            "openai": "gpt-4o-mini",
            "groq": "llama-3.3-70b-versatile",
            "deepseek": "deepseek-chat",
        }.get(self.provider, "gemini-2.5-flash")

    async def available(self) -> bool:
        return bool(self.api_key)

    async def generate(self, prompt: str, *, system: str | None = None,
                       json_mode: bool = False, max_tokens: int = 1024,
                       temperature: float = 0.4,
                       privacy_mode: str | None = None,
                       profile_uuid: str | None = None,
                       **_: Any) -> LLMResponse:
        from ..egress import fetch  # local import to avoid cycle on module load
        # Privacy mode must be 'cloud' for cloud API calls — guard at the call site.
        mode = privacy_mode or "cloud"
        if self.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
            body = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
            }
            if system:
                body["systemInstruction"] = {"parts": [{"text": system}]}
            if json_mode:
                body["generationConfig"]["responseMimeType"] = "application/json"
            r = await fetch(url, purpose="llm", mode=mode, profile_uuid=profile_uuid,
                            method="POST", json=body, audit_summary=f"gen prompt_chars={len(prompt)}")
            r.raise_for_status()
            data = r.json()
            text = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return LLMResponse(text=text, backend=self.name, model=self.model)
        if self.provider == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
            body = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature,
                    "messages": [{"role": "user", "content": prompt}]}
            if system:
                body["system"] = system
            r = await fetch(url, purpose="llm", mode=mode, profile_uuid=profile_uuid,
                            method="POST", headers=headers, json=body, audit_summary=f"gen prompt_chars={len(prompt)}")
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
            return LLMResponse(text=text, backend=self.name, model=self.model)
        if self.provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs.append({"role": "user", "content": prompt})
            body = {"model": self.model, "messages": msgs, "max_tokens": max_tokens, "temperature": temperature}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            r = await fetch(url, purpose="llm", mode=mode, profile_uuid=profile_uuid,
                            method="POST", headers=headers, json=body, audit_summary=f"gen prompt_chars={len(prompt)}")
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            return LLMResponse(text=text, backend=self.name, model=self.model)
        if self.provider == "groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs.append({"role": "user", "content": prompt})
            body = {"model": self.model, "messages": msgs, "max_tokens": max_tokens, "temperature": temperature}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            r = await fetch(url, purpose="llm", mode=mode, profile_uuid=profile_uuid,
                            method="POST", headers=headers, json=body, audit_summary=f"gen prompt_chars={len(prompt)}")
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            return LLMResponse(text=text, backend=self.name, model=self.model)
        if self.provider == "deepseek":
            url = "https://api.deepseek.com/chat/completions"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs.append({"role": "user", "content": prompt})
            body = {"model": self.model, "messages": msgs, "max_tokens": max_tokens, "temperature": temperature}
            r = await fetch(url, purpose="llm", mode=mode, profile_uuid=profile_uuid,
                            method="POST", headers=headers, json=body, audit_summary=f"gen prompt_chars={len(prompt)}")
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            return LLMResponse(text=text, backend=self.name, model=self.model)
        raise RuntimeError(f"Unknown API provider: {self.provider}")


# ── Hash-fallback embeddings (deterministic, lightweight) ────────────────────

class HashEmbedding(EmbeddingBackend):
    """Deterministic ~256-d projection from token hashes.

    Quality is poor compared to a real embedder, but the pipeline (clustering,
    MMR, persona similarity) runs end-to-end, which is what matters when Ollama
    isn't available. Tunable via FORYOU_EMBED_BACKEND=hash.
    """
    name = "hash"

    def __init__(self):
        self.dim = config.EMBED_DIM_HASH

    async def available(self) -> bool:
        return True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        toks = (text or "").lower().split()
        if not toks:
            return [0.0] * self.dim
        vec = [0.0] * self.dim
        # blake2b caps digest_size at 64 bytes → need ceil(dim*2 / 64) hash rounds.
        # Each round produces 32 signed shorts; we tile them across the vector.
        bytes_needed = self.dim * 2
        rounds = (bytes_needed + 63) // 64
        for tok in toks:
            buf = bytearray()
            for r in range(rounds):
                seed = tok.encode("utf-8") + bytes([r])
                buf.extend(hashlib.blake2b(seed, digest_size=64).digest())
            shorts = struct.unpack(f"{self.dim}h", bytes(buf[:bytes_needed]))
            for i, s in enumerate(shorts):
                vec[i] += s / 32768.0
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / norm for x in vec]


# ── Dispatcher ───────────────────────────────────────────────────────────────

_llm_singleton: LLMBackend | None = None
_emb_singleton: EmbeddingBackend | None = None


def get_llm_backend() -> LLMBackend:
    global _llm_singleton
    if _llm_singleton is None:
        b = config.LLM_BACKEND
        if b == "none":
            _llm_singleton = NoneLLM()
        elif b == "ollama":
            _llm_singleton = OllamaLLM()
        elif b == "llamacpp":
            _llm_singleton = LlamaCppLLM()
        elif b == "api":
            _llm_singleton = CloudAPILLM()
        else:
            log.warning("unknown LLM backend %s, falling back to 'none'", b)
            _llm_singleton = NoneLLM()
    return _llm_singleton


def get_embedding_backend() -> EmbeddingBackend:
    global _emb_singleton
    if _emb_singleton is None:
        b = config.EMBED_BACKEND
        if b == "ollama":
            _emb_singleton = OllamaEmbedding()
        else:
            _emb_singleton = HashEmbedding()
    return _emb_singleton


async def llm_available() -> bool:
    return await get_llm_backend().available()


def reset_backends_for_tests():
    """Test-only: drop singletons so config changes take effect."""
    global _llm_singleton, _emb_singleton
    _llm_singleton = None
    _emb_singleton = None
