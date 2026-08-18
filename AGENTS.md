# AGENTS.md

Cost and latency benchmark for speech-to-text plus PII redaction and summarization,
presented as a migration business case off the legacy Azure stack. See `README.md`
for the two architectures being compared.

## Layout

| Path | What it is |
| --- | --- |
| `data/stt.py` | The benchmark itself. Runs both STT architectures concurrently against a recording and scores them against a reference transcript. |
| `data/tts.py` | Generates the mock call recording (`data/mock-call.wav`) from `data/mock-call-transcript.txt` using MAI-Voice-2. |
| `data/*.txt`, `data/*.json` | Mock call fixtures and the last benchmark results. Files matching `*architecture-3*` are a retired batch variant, kept as a historical record and referenced by nothing. |
| `data/synthetic-call-corpus/`, `data/generate_synthetic_call_corpus.py` | Deterministic, PII-safe 100-call analytics corpus for the Fabric demo. Unrelated to the STT benchmark. |
| `data/load_corpus_to_warehouse.py`, `data/eval_data_agent.py` | Seed the corpus into a Fabric Warehouse, and score the Data Agent's answers against hand-written SQL baselines. |
| `backend/app/` | FastAPI service wrapping the benchmark: uploads, audio normalization, background jobs, results. |
| `frontend/` | TypeScript UI served by Bun. |
| `frontend/public/architecture/build_diagrams.py` | Generates the two architecture diagram pages. Edit it, not the generated HTML. |
| `infra/` | Foundry resource + DeepSeek deployment (`main.bicep`). |
| `infra/customer/` | The downstream analytics blueprint: Fabric capacity, Oracle schema, Mirroring prerequisites, the governed semantic model, and the Data Agent. |

## Frontend

The frontend is part of the architecture, not a demo bolted on the side. It is the
intended launching point for the benchmark: a user uploads a call recording and,
optionally, a reference transcript, and starts a run from the UI rather than by
invoking the CLI. The CLI entry point (`python data/stt.py`) remains for the
checked-in mock call, but new benchmark work should assume the frontend is how a
run gets started.

Today the UI can:

- run the checked-in mock call out of the box: `data/mock-call.wav` and
  `data/mock-call-transcript.txt` are seeded on startup as a built-in, non-deletable
  upload, so the benchmark is runnable without uploading anything,
- upload audio and, optionally, a reference transcript,
- transcribe uploaded audio through both architectures (wired to
  `stt.run_benchmark`), scoring word error rate when a reference transcript is
  present,
- poll a run and show per-architecture latency, accuracy, and transcripts.

It has five views. **Business case** is the default and the seller-facing one: the
handoff scenario and the retirement hook, then the headline saving, the measured
deltas, a projection driven by three discount inputs (Azure AI Speech, Azure AI
Language, Foundry model) plus call volume, and — last — the upload form.
**Architectures** shows the two stacks as tabs over their pipeline diagrams.
**Post-call analytics** explains the Oracle-to-Fabric architecture and provides the
seller talk track. **Call evidence** links every artifact back to its file in this
repository. **Technical details** keeps the full measured tables and the run history.

Every view that reads a report does so from one *selected recording*
(`frontend/src/source.ts`): the built-in sample call, or any completed run of an
uploaded one. The source bar names it on those views (Architectures and Post-call
analytics are static and hide the bar), offers a picker only once there is more than
one, and a finished run is selected automatically.

## Rules

- `data/stt.py` has exactly one benchmark implementation. `run_benchmark()` is the
  shared entry point for both the CLI and the API; do not fork a second copy of the
  orchestration for the web path.
- `data/mock-call.wav` and `data/mock-call-transcript.txt` are the default benchmark
  inputs everywhere: they are the defaults of `AUDIO_PATH` / `TRANSCRIPT_PATH` for
  the CLI, they ship in the backend image, and they are seeded as the `mock-call`
  upload for the UI. Keep those three in sync.
- Uploaded audio is normalized to 16-bit mono PCM WAV before it reaches the
  benchmark. Audio already in that form is passed through untouched so scores
  reflect the original samples, not a resampled copy.
- Real-time engines stream at 1x, so a run takes roughly as long as the recording.
  Anything user-facing must be asynchronous and pollable.
- Auth is Microsoft Entra ID via `DefaultAzureCredential`; key-based auth is
  disabled on the Speech resource. The backend container gets credentials from a
  mounted Azure CLI profile.
- Use `bun` for the frontend, not `node`.
- All unit prices and discounts live in `frontend/src/pricing.ts`; there is no pricing
  code in `backend/` or `data/`. Never restate a discount rate in prose — derive the
  wording from the settings, or it will lie the first time someone moves a slider.
- The app must always be explicit about *which* recording it is reporting on. Anything
  that reads a report takes it from the selected source, never from the built-in one
  directly, and repository links are shown only for the built-in call — they describe
  files that exist, and a user's own upload has none.
- A saved benchmark report can contain architectures and engines that no longer exist.
  Everything the UI renders is filtered through the allow-lists in
  `frontend/src/catalog.ts`; do not sort report keys without filtering them first.
- The architecture diagram pages are generated. Edit
  `frontend/public/architecture/build_diagrams.py` and re-run it from the repository
  root; the shared Fabric analytics tail is written once and must stay that way.

## Commands

```bash
# Backend + frontend on localhost
docker compose up --build          # backend on :8000
cd frontend && bun install && bun run dev   # UI on :3000

# Benchmark the checked-in mock call directly
uv run python data/stt.py

# Frontend type checking and tests
cd frontend && bun run typecheck && bun test

# Backend tests
uv run --with pytest python -m pytest backend/tests

# Regenerate the architecture diagrams
python3 frontend/public/architecture/build_diagrams.py
```
