# PYTR For You — sidecar

Optional Docker sidecar that adds a multi-feed personal recommendation layer
to PYTR. When this container is not running, PYTR behaves identically to
upstream — see `.claude/discovery-plan.md` for the design.

## Quick start

```bash
# Start the sidecar (and the rest of PYTR):
docker compose --profile foryou --profile cookies up -d --build
```

`docker compose --profile foryou ...` is mandatory — without the profile flag
the sidecar simply isn't created and PYTR runs unchanged.

## LLM backend (re-distributable)

The For You container image **does not bundle multi-gigabyte models or
proprietary keys**, and it does not run its own LLM. It is a thin client that
talks to an **OpenAI-compatible `/v1` endpoint** over HTTP. By default that
endpoint is the shared Ollama service (a separate project, `Containers/ollama`),
but it can be any local server or remote provider. Pick a backend via
`FORYOU_LLM_BACKEND`:

| Backend     | Setup                                                                 |
|-------------|-----------------------------------------------------------------------|
| `none`      | Quality / Fresh / Trending / Community Picks still work. Discover/Surprise/persona degrade gracefully. |
| `openai` *(default)* | OpenAI-compatible client. Set `FORYOU_LLM_BASE_URL` (default `http://host.docker.internal:11434/v1`), `FORYOU_LLM_MODEL`, optional `FORYOU_LLM_API_KEY`. Works with the shared Ollama, vLLM, llama.cpp, LM Studio, or a remote provider. |
| `ollama`    | Legacy. Talks Ollama's native `/api/*` protocol at `FORYOU_OLLAMA_URL`. |
| `llamacpp`  | Optional. Add `llama-cpp-python` to `requirements.txt`, drop a GGUF into `data/foryou/models/`, set `FORYOU_LLAMACPP_MODEL_PATH`. |
| `api`       | Curated, privacy-gated cloud (Gemini / Anthropic / OpenAI / Groq / DeepSeek). Set `FORYOU_API_PROVIDER`, `FORYOU_API_KEY`. **Privacy mode must be `cloud`.** |

Embedding backends mirror this: `openai` *(default)* and `ollama` hit the same
endpoint; `hash` is a deterministic 256-d fallback — degraded quality but the
pipeline runs end-to-end with no external deps.

### Default flow

1. **Once**, bring up the shared LLM service (separate project):

   ```bash
   cd /path/to/Containers/ollama
   ./setup.sh            # GPU autodetect
   docker compose up -d  # serves http://<host>:11434/v1, pulls default models
   ```

2. **Then**, start PYTR + For You:

   ```bash
   docker compose build && docker compose down && docker compose up -d
   ```

The foryou container reaches the host service via
`host.docker.internal:11434` (wired through `extra_hosts` in the compose file).
While the shared service pulls models, foryou already serves — only Discover /
Surprise / persona wait for the LLM.

### Point at something else

Change one variable. Another machine on the LAN:

```bash
# .env
FORYOU_LLM_BASE_URL=http://192.168.1.50:11434/v1
```

A remote OpenAI-compatible provider:

```bash
# .env
FORYOU_LLM_BASE_URL=https://api.groq.com/openai/v1
FORYOU_LLM_MODEL=llama-3.3-70b-versatile
FORYOU_LLM_API_KEY=gsk_...
```

GPU passthrough and model management live with the shared service — see
`Containers/ollama/README.md`.

## Privacy modes

Three presets over a single egress allowlist:

* **Fortress** — RSS, Tournesol, SponsorBlock, Wikidata, MusicBrainz, Last.fm only.
  No YT search, no cloud, no community feeds.
* **Balanced** *(default)* — adds anonymous YT search/Charts, Reddit & HN community link feeds.
* **Cloud** — adds LLM API providers (Gemini/Anthropic/OpenAI/Groq/DeepSeek).

Switch from Settings → For You. Mode applies per-profile.

### Note: ranking signal

Embeddings are built from each candidate's title + a short description slice.
Bulk YouTube transcript fetching was tried but removed — aggressive caption
requests triggered YouTube throttling that bled into the user-facing subtitle
feature. Content-aware ranking now relies on title/description plus the
community/curation signals (Tournesol, Reddit, HN, RSS).

## Data layout

```
data/foryou/
  ├── foryou.db        # SQLite (candidates, taste profiles, feeds, feedback, audit log)
  ├── imports/         # Shoulder-data imports (Last.fm, Letterboxd, …)
  └── models/          # Optional llamacpp gguf files
```

The container mounts the same `./data` volume as PYTR and reads PYTR's
`cookies.txt` read-only.

## Tests

```bash
pip install pytest pytest-asyncio
python -m pytest tests/foryou
```
