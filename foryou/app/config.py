# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runtime configuration for the For You sidecar — all knobs in one place."""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("FORYOU_DATA_DIR", "/app/data/foryou"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
IMPORTS_DIR = DATA_DIR / "imports"
IMPORTS_DIR.mkdir(exist_ok=True)
MODELS_DIR = DATA_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "foryou.db"

# PYTR internal URL (used to call /api/internal/profile-export/{uuid})
PYTR_INTERNAL_URL = os.environ.get("FORYOU_PYTR_URL", "http://pytr:8000")

# LLM backend selection: none | openai | ollama | llamacpp | api
LLM_BACKEND = os.environ.get("FORYOU_LLM_BACKEND", "openai").lower()

# OpenAI-compatible backend (default) — a plain HTTP client against any /v1
# endpoint: the shared Ollama service, another local server (vLLM, llama.cpp,
# LM Studio), or a remote provider. This is how foryou stays a thin client.
LLM_BASE_URL = os.environ.get("FORYOU_LLM_BASE_URL", "http://host.docker.internal:11434/v1")
LLM_MODEL = os.environ.get("FORYOU_LLM_MODEL", "gemma3:4b")
LLM_API_KEY = os.environ.get("FORYOU_LLM_API_KEY", "")
EMBED_MODEL = os.environ.get("FORYOU_EMBED_MODEL", "embeddinggemma")

# Native Ollama backend (legacy/optional) — talks Ollama's own /api/* protocol.
OLLAMA_URL = os.environ.get("FORYOU_OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("FORYOU_OLLAMA_MODEL", "gemma3:4b")
OLLAMA_EMBED_MODEL = os.environ.get("FORYOU_OLLAMA_EMBED_MODEL", "embeddinggemma")
OLLAMA_AUTOPULL = os.environ.get("FORYOU_OLLAMA_AUTOPULL", "1") == "1"

# llama.cpp bundled-model path (only used when LLM_BACKEND=llamacpp)
LLAMACPP_MODEL_PATH = os.environ.get("FORYOU_LLAMACPP_MODEL_PATH", str(MODELS_DIR / "gemma-3-4b-it-Q4_K_M.gguf"))

# Cloud API
API_PROVIDER = os.environ.get("FORYOU_API_PROVIDER", "gemini").lower()
API_KEY = os.environ.get("FORYOU_API_KEY", "")
API_MODEL = os.environ.get("FORYOU_API_MODEL", "")

# Embedding backend: openai | ollama | hash
EMBED_BACKEND = os.environ.get("FORYOU_EMBED_BACKEND", "openai").lower()
EMBED_DIM_HASH = 256  # dimension when using the deterministic hash fallback

COOKIES_FILE = Path(os.environ.get("FORYOU_COOKIES", "/app/data/cookies.txt"))

# Privacy modes share a single egress allowlist engine.
PRIVACY_MODE_DEFAULT = os.environ.get("FORYOU_PRIVACY_MODE_DEFAULT", "balanced")

# Listening port (mostly informational — the proxy reaches us at 8000 internally)
PORT = int(os.environ.get("FORYOU_PORT", "8000"))
