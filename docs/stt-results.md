# STT Benchmark: Findings, Results, and Gotchas

Context document for agents working on the STT stage of this repo. Everything here
was verified empirically against live Azure resources - no claim below is
speculative unless explicitly flagged as unverified.

- **Resource**: `charter-stt-pii-resource`, resource group `rg-charter-stt-pii`, region `eastus`
- **Subscription**: `fd918039-a89e-49a7-8e32-af614b3765f9`
- **Scripts**: `data/tts.py` (generate audio), `data/stt.py` (benchmark)
- **Runtime**: macOS, Python 3.14, `uv`-managed venv. Run with `uv run python data/stt.py`.

---

## 1. What is being measured

A 505.8s synthetic two-speaker support call (`data/mock-call.wav`, 24kHz mono),
generated from the 989-word reference transcript `data/mock-call-transcript.txt`.
Three STT variants are run **concurrently**, so a full benchmark pass costs roughly
one call duration rather than three.

| Variant | Engine | Transport | Mode |
| --- | --- | --- | --- |
| 1 | Azure Speech (standard) | Speech SDK `SpeechRecognizer` | real-time, incremental |
| 2 | MAI-Transcribe-1.5 | Voice Live WebSocket | real-time, utterance micro-batch |
| 3 | MAI-Transcribe-1.5 | Fast-transcription REST | batch, post-call |

These variant numbers are independent of the architecture sections in the README.
Variants 1 and 2 are the STT stages of Architecture 1 and Architecture 2; variant 3
is Architecture 2's model in post-call batch mode.

---

## 2. Results

| Variant | WER | Accuracy | Mean lag | p95 lag | Transcript ready | Segments |
| --- | --- | --- | --- | --- | --- | --- |
| 1. Azure Speech real-time | 5.66% | 94.34% | 0.77s | 1.04s | 506.0s | 73 |
| 2. MAI-Transcribe-1.5 real-time | **2.93%** | **97.07%** | 0.80s | 0.91s | **505.9s** | 102 |
| 3. MAI-Transcribe-1.5 batch | 4.04% | 95.96% | 7.8s turnaround | n/a | 513.6s | 1 |

Error breakdown against the 989-word reference:

| Variant | Substitutions | Deletions | Insertions | Hits |
| --- | --- | --- | --- | --- |
| 1 | 27 | 24 | 5 | 938 |
| 2 | 14 | 6 | 9 | 969 |
| 3 | 21 | 5 | 14 | 964 |

**Headline**: MAI-Transcribe-1.5 achieves roughly half the word error rate of the
classic Speech SDK recognizer at equivalent streaming latency, and is *more* accurate
when fed VAD-aligned utterances than when handed the whole call in one request.

Raw metrics are persisted to `data/stt-benchmark-results.json`. Per-engine
transcripts are written to `data/transcript-architecture-{1,2,3}-*.txt`.

### What WER means

Word Error Rate is the standard accuracy metric for speech recognition. The
hypothesis transcript is aligned to the reference with minimum edit distance, and:

```
WER = (substitutions + deletions + insertions) / reference_word_count
```

- **Substitution** - wrong word ("Whitaker" for "Whittaker")
- **Deletion** - reference word missing from the hypothesis
- **Insertion** - extra word not in the reference

Lower is better. `accuracy = 1 - WER`. WER can exceed 100% because insertions are
unbounded. Computed here with `jiwer`.

---

## 3. Pricing

List price, eastus, at the time of writing.

| Engine | Rate | Source |
| --- | --- | --- |
| MAI-Transcribe-1.5 | $0.36 / audio hour | <https://ai.azure.com/catalog/models/MAI-Transcribe-1.5> |
| Azure Speech, standard STT | $1.00 / audio hour | <https://azure.microsoft.com/en-us/pricing/details/speech/> |
| Azure Speech, diarization add-on | +$0.30 / audio hour | same |

**Diarization is not enabled in any benchmarked variant** and is excluded from the
figures below. Variant 1 uses `SpeechRecognizer`, not `ConversationTranscriber`, and
neither MAI path sets a speaker-separation option. Speaker attribution in this repo
comes from the transcript's own `REP:` / `CUSTOMER:` labels, not from the STT service.
If a future variant needs service-side diarization, add $0.30/hr, which takes Azure
Speech to $1.30/hr and widens the gap to 3.6x.

Billing is on **audio duration**, not wall-clock or request count, so variants 2 and 3
cost the same despite very different transports. The 505.8s call is 0.140507 hours.

| Engine | Per call | 1,000 calls | 100,000 calls | Per 1,000 audio hours |
| --- | --- | --- | --- | --- |
| MAI-Transcribe-1.5 (variants 2 and 3) | $0.0506 | $50.58 | $5,058 | $360 |
| Azure Speech standard (variant 1) | $0.1405 | $140.51 | $14,051 | $1,000 |
| *Azure Speech + diarization (not used)* | *$0.1827* | *$182.66* | *$18,266* | *$1,300* |

MAI-Transcribe-1.5 is **2.78x cheaper** than standard Azure Speech STT while scoring
roughly half the WER. On this workload it wins on both axes simultaneously, which is
the single most important finding in this document. At 100k calls the saving is
about **$8,993**.

Caveats: list price only - no commitment tiers, reserved capacity, or negotiated
discounts. Excludes the LLM cost of Voice Live's mandatory `model=gpt-4.1` parameter
(see section 6), TTS costs for generating the test audio, egress, and the downstream
PII-redaction and summarization stages.

---

## 4. The two latency questions

Reporting a single "latency" number is misleading here, so two are tracked.

- **Finalization lag** (mean / p95) - delay between the end of an utterance's audio
  and the arrival of its final transcript. This is what matters for live agent
  assist, mid-call PII redaction, or anything acting on the call while it happens.
  Only the real-time variants have it. Measured identically on both real-time paths.
- **Transcript ready** - seconds from the *start of the call* until the complete
  transcript exists. Real-time transcribes concurrently with the call, so it finishes
  essentially when the caller hangs up. Batch cannot start until the call ends, so it
  pays the full call duration *plus* turnaround (505.8s + 7.8s = 513.6s).

Wall-clock runtime alone is useless for the real-time variants: it is pinned to the
audio length (~506s for both), so it measures the call, not the engine. Batch's 7.8s
turnaround looks fast in isolation but is the *last* leg of a 513.6s path - batch is
the slowest end-to-end option, not the fastest.

Batch turnaround is also unstable: 3.1s in an isolated run, 7.8s when contending with
two concurrent streaming sessions. It is a single-sample measurement, not an average.

---

## 5. Methodology, and where the comparison is not apples-to-apples

Read this before quoting any number.

### MAI has no true incremental streaming path

MAI-Transcribe never emits `conversation.item.input_audio_transcription.delta` - only
`.completed`. Variant 1 streams partial hypotheses *while the speaker is still
talking*; variant 2 cannot. The lag column does not capture this capability
difference. If the product needs sub-utterance partials, variant 1 or
`gpt-live-transcribe` are the only options.

### Chunking is VAD-aligned, and only applied to MAI

`stt.py` runs a local RMS-based VAD and commits at natural pauses. **This is applied
only to variant 2.** Variant 1 receives one unbroken stream and the *service* does its
own endpointing; nothing is clipped locally. The local VAD exists solely to give MAI
equivalent utterance boundaries.

Even then, MAI keeps a **single persistent WebSocket** open for the whole call - a
commit marks a boundary in the server-side buffer, it is not a fresh API call per
utterance.

VAD parameters: `VAD_RMS_THRESHOLD=150`, `VAD_MIN_SILENCE_SECONDS=0.5`,
`VAD_MAX_UTTERANCE_SECONDS=20`, yielding 103 utterances with a median of 2.6s -
comparable to the SDK's 73 service-side segments. Note `audioop` was removed in
Python 3.13+, so RMS is computed manually with `array` + `math.sqrt`.

### Why VAD chunking beats a fixed clock

Committing on a fixed interval slices sentences mid-phrase and starves the model of
context. Measured sweep (run in parallel via asyncio - 121s total instead of ~10min):

| Fixed commit interval | WER | Worst-case word delay |
| --- | --- | --- |
| 3s | 11.11% | ~3.9s |
| 5s | 5.15% | ~6.0s |
| 10s | 2.97% | ~10.9s |
| 20s | 2.60% | ~21.1s |
| 30s | 2.60% | ~31.1s |

Accuracy plateaus around 20s while latency keeps degrading linearly. A 30s interval -
a common default - is strictly worse than 20s. VAD alignment gets 2.93% WER at 0.80s
mean lag, beating every fixed interval on the latency/accuracy tradeoff.

### Audio must be streamed at 1x

A file-based `AudioConfig(filename=...)` lets the Speech SDK consume audio about
**twice as fast as real time**, which made the lag metric report 0.00s and rendered it
meaningless. The SDK is now fed through a throttled `PullAudioInputStream`
(`ThrottledPcmStream` in `stt.py`).

### Scoring folds numbers to a canonical form

The engines apply different inverse text normalization ("three hundred" vs "300",
"eleventh" vs "11th", digit grouping). Without folding, formatting differences alone
inflated WER by roughly 2 points (5.44%/6.13% became 2.53%/4.04%). `fold_numbers()`
digitizes number words, strips ordinal suffixes, and joins adjacent digit runs; the
normalizer also expands `mr/ms/dr/ok/wi-fi/e-mail/$N/%`.

Remaining errors after folding are genuine: `Whitaker` to `Whittaker`, `Northstar` to
`North Star`, and digit slips in phone and card numbers - the last being directly
relevant to the PII stage.

### Synthetic audio flatters every engine

No overlapping speech, crosstalk, or line noise. Absolute WER and lag will be better
here than on real recordings. **The relative comparison is the useful part.**

---

## 6. API details and gotchas

### Authentication

Local key auth is **disabled** (`disableLocalAuth: true`). `DefaultAzureCredential` is
mandatory.

- Speech SDK: `speech_config.authorization_token = f"aad#{RESOURCE_ID}#{token}"`,
  with `SpeechConfig(endpoint=...)`.
- REST and WebSocket: plain `Authorization: Bearer {token}`.
- Token scope: `https://cognitiveservices.azure.com/.default`.
- Resource ID:
  `/subscriptions/fd918039-a89e-49a7-8e32-af614b3765f9/resourceGroups/rg-charter-stt-pii/providers/Microsoft.CognitiveServices/accounts/charter-stt-pii-resource`

**Running in a container (ACA/AKS)**: `DefaultAzureCredential` resolves to
`ManagedIdentityCredential`, so no code changes are needed - but the managed identity
must be granted `Cognitive Services Speech User` on the Speech account. That role's
data actions cover both `SpeechServices/*/transcriptions/*` (fast transcription) and
`SpeechServices/voicelive/realtime/*` (Voice Live). A user-assigned identity
additionally requires the `AZURE_CLIENT_ID` env var. Locally this is masked because
the developer identity reaches the resource via subscription-scope `Owner`, not an
explicit assignment. See the README for the full checklist.

**Token refresh is not implemented.** `get_token()` is called once at startup
(`stt.py:613`) and Entra tokens expire in ~1 hour. Fine for a 9-minute benchmark,
broken for a long-lived service or a call over an hour.

### MAI-Transcribe-1.5 is not a deployable model

There is no `az cognitiveservices account deployment create` step and it does not
appear in the model catalog. It is selected **per request**. Do not waste time looking
for a deployment.

### Batch (fast transcription)

```
POST /speechtotext/transcriptions:transcribe?api-version=2025-10-15
definition = {"locales": ["en"],
              "enhancedMode": {"enabled": true,
                               "model": "mai-transcribe-1.5",
                               "transcribeStyle": "verbatim"}}
```

**Critical gotcha**: `api-version=2024-11-15` returns HTTP 200 but *silently ignores*
`enhancedMode`, so you get the standard model and think you benchmarked MAI.
`2025-05-15-preview` and `2025-11-15` return 404. Only `2025-10-15` works.

### Real-time (Voice Live)

```
wss://{resource}.services.ai.azure.com/voice-live/realtime?api-version=2026-04-10&model=gpt-4.1
session.input_audio_transcription = {"model": "mai-transcribe"}
```

Both `.services.ai.azure.com` and `.cognitiveservices.azure.com` hosts work. The
`model=gpt-4.1` parameter is required by the endpoint even though only transcription
is used - budget for its cost separately if this reaches production.

**Voice Live limitations, all verified empirically** (and contradicting the
documentation and initial research):

- Server VAD returns a transcript **only for the first turn** of a session. Every
  subsequent turn returns `""`. Tested and failed: `create_response: true`, including
  `"audio"` in modalities, manual `response.create` after each commit, `server_vad`,
  `azure_semantic_vad`.
- This also affects the `azure-speech` model, so it is a Voice Live bug, not
  MAI-specific.
- `conversation.item.retrieve` confirms items genuinely contain `"transcript": ""`
  server-side. Items also expire quickly, producing "does not exist" errors.
- **Workaround**: `turn_detection: null` plus manual `input_audio_buffer.commit`.
  This is why the local VAD exists at all.
- Only `mai-transcribe` and `azure-speech` are accepted as
  `input_audio_transcription.model`. `gpt-4o-transcribe`, `gpt-live-transcribe`, and
  `whisper-1` are rejected with WebSocket close 1008.
- Requires `ping_interval=20, ping_timeout=60` or connections drop on long runs.
- Commits under ~0.2s produce a harmless `input_audio_buffer_commit_empty` error
  (filtered in `stt.py`).

### Classic Speech SDK

Cannot select MAI-Transcribe. `PropertyId.SpeechServiceConnection_RecoModelName` has
no effect (tested). `result.offset` and `result.duration` are in 100-nanosecond ticks,
so divide by `1e7` for seconds.

### For genuinely incremental modern STT

`gpt-live-transcribe` (capability `realtimeTranscription: true`, emits `.delta`) via
`/openai/v1/realtime?intent=transcription`. Requires a model deployment, is **not
available in eastus** (eastus2 / swedencentral / centralus only), and costs ~$1.02/hr
- nearly 3x MAI. **Unverified**: its presence and capability flags were confirmed in
the eastus2 catalog, but its `.delta` behavior was never actually tested.

---

## 7. Reproducing

```bash
uv run python data/tts.py    # regenerates data/mock-call.wav - see warning below
uv run python data/stt.py    # runs all three variants concurrently, ~9 minutes
```

**Do not regenerate `mock-call.wav` casually.** TTS output is not deterministic, so a
new WAV invalidates every number in this document.

`data/stt.py` exposes `run_benchmark(audio_path=..., reference_path=..., on_progress=...)`
for programmatic use, with an `ENGINES` dict and per-engine exception isolation, so one
failing variant does not abort the run. Scoring is skipped when no reference transcript
is supplied.

---

## 8. Open items

- `gpt-live-transcribe` has never been run - the only genuinely incremental option
  remains unmeasured.
- Batch turnaround is a single sample and varies 3.1s to 7.8s with load. No run
  averaging is performed.
- No real call recordings have been tested; all conclusions rest on synthetic audio.
- Entra token refresh is not implemented - see section 6.
- Cost figures cover STT only. The PII-redaction and summarization stages described in
  the README are not yet implemented or priced.
