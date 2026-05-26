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
proprietary keys**. Instead, a separate `ollama` sidecar container is bundled
under the same `foryou` compose profile; it shares the LLM with anything else
that needs one. Pick a backend via `FORYOU_LLM_BACKEND`:

| Backend     | Setup                                                                 |
|-------------|-----------------------------------------------------------------------|
| `none`      | Quality / Fresh / Trending / Community Picks still work. Discover/Surprise/persona degrade gracefully. |
| `ollama` *(default)* | Zero setup. The bundled `ollama` container auto-pulls the configured models on first boot. Models live in the `ollama-models` Docker volume — persists across rebuilds. |
| `llamacpp`  | Optional. Add `llama-cpp-python` to `requirements.txt`, drop a GGUF into `data/foryou/models/`, set `FORYOU_LLAMACPP_MODEL_PATH`. |
| `api`       | Cloud (Gemini / Anthropic / OpenAI / Groq / DeepSeek). Set `FORYOU_API_PROVIDER`, `FORYOU_API_KEY`. **Privacy mode must be `cloud`.** |

Embedding backends mirror this: `ollama` (real, via the bundled sidecar) or
`hash` (deterministic 256-d fallback — degraded quality but the pipeline runs
end-to-end with no external deps).

### Default flow (1-click)

```bash
# One-time:
./setup-foryou.sh
# Every time:
docker compose build && docker compose down && docker compose up -d
```

`setup-foryou.sh` autodetects your OS + GPU vendor + group IDs and writes
both `.env` and (if a GPU is found) `docker-compose.override.yml` with the
right device passthrough block. Re-run it after a GPU swap or driver upgrade
— it's idempotent.

First boot pulls ~3.5 GB of models in the background. While that happens the
foryou container is already serving — only Discover / Surprise / persona wait
for the LLM. Track progress with:

```bash
docker compose logs -f foryou
# look for: ollama pull gemma3:4b: pulling manifest (NN%)
```

### GPU passthrough

Handled automatically by `setup-foryou.sh`:

| Detected | What the script does |
|---|---|
| NVIDIA | Picks `ollama/ollama:latest` + writes `deploy.resources.reservations.devices` (requires `nvidia-container-toolkit` on host — script warns if missing) |
| AMD    | Picks `ollama/ollama:rocm` + binds `/dev/kfd` + `/dev/dri` + adds video/render groups (requires `amdgpu` kernel driver) |
| None   | CPU image, no override file |

### Override: point at an existing Ollama

If you already run Ollama on the host or another machine:

```bash
# .env
FORYOU_OLLAMA_URL=http://192.168.1.50:11434
FORYOU_OLLAMA_AUTOPULL=0
```

Then drop the bundled service: leave `foryou` out of `COMPOSE_PROFILES` for
the ollama container only — easiest way is `docker compose up -d pytr foryou
discovery cookie-beast` (omit `ollama`).

## Privacy modes

Three presets over a single egress allowlist:

* **Fortress** — RSS, Tournesol, SponsorBlock, Wikidata, MusicBrainz, Last.fm only.
  No YT search, no cloud, no community feeds.
* **Balanced** *(default)* — adds anonymous YT search/Charts, Reddit & HN community link feeds,
  and YouTube transcript fetching for content-aware ranking.
* **Cloud** — adds LLM API providers (Gemini/Anthropic/OpenAI/Groq/DeepSeek).

Switch from Settings → For You. Mode applies per-profile.

### Trade-off: Fortress vs. transcript-aware ranking

Fortress mode runs the entire pipeline on title + description only — no
YouTube transcript fetching. This is by design (Fortress refuses any YouTube
egress beyond the public RSS feed) but it does cost ranking precision.
Balanced+ enables the background `enrich_transcripts` job, which pulls and
caches captions for ~40 candidates every 2 hours and feeds them into the
embedding step. Empirical research suggests +5–15 percentage points in
precision@k once transcripts are available.

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
