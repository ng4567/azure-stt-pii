# Repository readiness assessment

## Verdict

The implementation is suitable for an engineering benchmark and is operationally ready with deployment caveats, but its current single synthetic fixture is not enough for production-grade architecture selection.

| Component | Build | Completeness | Deployability |
| --- | --- | --- | --- |
| FastAPI benchmark backend | PASS | PASS | WARN |
| Bun TypeScript frontend | PASS | PASS | WARN |

## Resolved release blocker

The backend image previously omitted `data/mock-call-pii-ground-truth.json`, causing built-in upload seeding to be skipped. The Dockerfile now copies the fixture and `.dockerignore` explicitly includes it.

## Benchmark strengths

- All STT engines run concurrently against the same audio and use one shared orchestration.
- Real-time audio is paced at 1x, while batch readiness includes call duration plus turnaround.
- Stereo channels preserve authoritative REP/CUSTOMER identity without diarization differences.
- Outputs share one schema and downstream architectures fan out concurrently.
- STT uses WER and per-participant WER; PII uses independent, hash-bound annotations with precision, recall, F1, category accuracy, leakage, and alignment.
- Pricing uses measured usage and explicitly applies the supplied discounts.

## Benchmark validity caveats

- Evidence comes from one synthetic, TTS-generated support call; no real recordings, accents, codecs, background noise, crosstalk, telephony bandwidth, or domain diversity are represented.
- Published latency and batch turnaround are single samples, with no repeated runs, variance, confidence intervals, warm/cold separation, or throttling analysis.
- Architecture 1 changes both STT and downstream processing relative to Architectures 2/3, so an end-to-end difference cannot be attributed to one component.
- Architectures 2 and 3 share DeepSeek downstream logic, making them a valid STT-mode comparison but not independent end-to-end alternatives.
- Summaries are sanitized but not scored for factuality, completeness, issue/resolution correctness, or hallucination.
- Exact-span PII scoring is rigorous for annotated/projectable entities, but the 26-entity set is small and STT-unaligned PII is excluded from redaction false negatives; alignment must be interpreted beside leakage.
- Cost excludes hosting, storage, observability, networking, retries outside reported usage, and the possible Voice Live host-model charge.

## Deployment caveats

- Bicep provisions only the Foundry resource and DeepSeek deployment, not Speech, Language, the backend/frontend application, identity role assignments, storage, or observability.
- Uploaded files and result JSON use local disk. A multi-replica Container Apps deployment needs Azure Files or Blob Storage and a durable/shared job state design.
- The frontend has no production Dockerfile or Azure hosting definition.
- The worktree contains an unrelated tracked deletion of `main.py` and untracked `infra/`; deployment files are therefore not part of the committed release state.

## Post-fix re-evaluation

The backend build definition now contains every file required by `uploads.ensure_builtins()`. Build and completeness checks pass statically. Deployment remains WARN because durable storage, a production frontend target, and complete application infrastructure are not yet defined.
