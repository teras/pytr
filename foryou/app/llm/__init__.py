# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""LLM + embedding abstraction.

Four interchangeable LLM backends so the For You container is **re-distributable**
without bundling multi-gigabyte models or proprietary API keys:

  * ``none``     — feeds that don't need an LLM still run (Fresh, Quality, Trending,
                   Community Picks). Discover/Surprise/persona degrade gracefully.
  * ``ollama``   — BYO local install. Image stays small (~300 MB). Default.
                   Reaches host via ``host.docker.internal:11434``.
  * ``llamacpp`` — bundled gguf via llama-cpp-python. Self-contained but heavy
                   image. Optional; only loaded if the package is installed AND
                   FORYOU_LLM_BACKEND=llamacpp.
  * ``api``      — cloud (Gemini / Anthropic / OpenAI / Groq / DeepSeek). Requires
                   privacy_mode=cloud and an API key.

Embeddings have two backends: ``ollama`` (real, via the host install) and ``hash``
(deterministic 256-d hash, used when nothing better is available — the pipeline
runs end-to-end but with degraded clustering quality).
"""
from .backend import (
    EmbeddingBackend,
    LLMBackend,
    LLMResponse,
    get_embedding_backend,
    get_llm_backend,
    llm_available,
)

__all__ = [
    "EmbeddingBackend",
    "LLMBackend",
    "LLMResponse",
    "get_embedding_backend",
    "get_llm_backend",
    "llm_available",
]
