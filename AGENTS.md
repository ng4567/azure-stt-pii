# AGENTS.md

Cost and latency benchmark for speech-to-text plus PII redaction and summarization.
See `README.md` for the architectures being compared.

## Layout

| Path | What it is |
| --- | --- |
| `data/stt.py` | The benchmark itself. Runs all three STT architectures concurrently against a recording and scores them against a reference transcript. |
| `data/tts.py` | Generates the mock call recording (`data/mock-call.wav`) from `data/mock-call-transcript.txt` using MAI-Voice-2. |
| `data/*.txt`, `data/*.json` | Mock call fixtures and the last benchmark results. |
| `backend/app/` | FastAPI service wrapping the benchmark: uploads, audio normalization, background jobs, results. |
| `frontend/` | TypeScript UI served by Bun. |

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
- upload audio and/or a reference transcript,
- transcribe uploaded audio through all three architectures (wired to
  `stt.run_benchmark`), scoring word error rate when a reference transcript is
  present,
- poll a run and show per-architecture latency, accuracy, and transcripts.

It is built to grow into the full benchmark suite front end: PII redaction and
summarization stages, and cost comparison, are expected to surface here too.

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

## Commands

```bash
# Backend + frontend on localhost
docker compose up --build          # backend on :8000
cd frontend && bun install && bun run dev   # UI on :3000

# Benchmark the checked-in mock call directly
uv run python data/stt.py

# Frontend type checking
cd frontend && bun run typecheck
```
