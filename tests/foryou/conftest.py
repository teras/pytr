"""Pytest config: put foryou on the import path and isolate DB per session."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORYOU_ROOT = ROOT / "foryou"

# Make ``app`` importable as ``app`` (matching the container layout).
sys.path.insert(0, str(FORYOU_ROOT))

# Isolated data dir per test session — outside the real /app/data tree.
_TMP = tempfile.mkdtemp(prefix="foryou-tests-")
os.environ["FORYOU_DATA_DIR"] = _TMP
os.environ["FORYOU_LLM_BACKEND"] = "none"
os.environ["FORYOU_EMBED_BACKEND"] = "hash"
