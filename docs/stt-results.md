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

A synthetic two-speaker support call generated from the 989-word reference transcript
`data/mock-call-transcript.txt`. Two fixtures are retained:

- `data/mock-call.wav` - the original 505.8s, 24kHz mono baseline used for the
  published results below.
- `data/mock-call-stereo.wav` - a separate 24kHz stereo fixture with REP on channel 0
  and CUSTOMER on channel 1. `data/mock-call-stereo-turns.json` records its reference
  channel map and turn timing.

The frontend treats the stereo fixture as its single default input and immediately
shows `data/mock-call-stereo-stt-benchmark-results.json` as a cached architecture
comparison. The mono fixture is retained for historical CLI reproduction, not shown
as a second frontend benchmark. One frontend action runs either uploaded audio, the
default audio when no audio is supplied, or the default audio with an uploaded
transcript-only reference.

Three STT variants are run **concurrently**, so a full benchmark pass costs roughly
one call duration rather than three.

| Variant | Engine | Transport | Mode |
| --- | --- | --- | --- |
| 1 | Azure Speech (standard) | Speech SDK `SpeechRecognizer` | real-time, incremental |
| 2 | MAI-Transcribe-1.5 | Voice Live WebSocket | real-time, utterance micro-batch |
| 3 | MAI-Transcribe-1.5 | Fast-transcription REST | post-call VAD utterances |

These variants map to the end-to-end architectures in the README: variant 1 is
Architecture 1, variant 2 is Architecture 2, and variant 3 is Architecture 3.

---

## 2. Results

### Dual-channel, turn-ready run

Refreshed August 6, 2026 from `mock-call-stereo.wav` (504.168s), with REP on channel 0,
CUSTOMER on channel 1, and 113 channel-local VAD utterances.

| Variant | WER | Accuracy | Mean lag | p95 lag | Transcript ready | Turns |
| --- | --- | --- | --- | --- | --- | --- |
| 1. Azure Speech real-time | 5.36% | 94.64% | 0.77s | 0.93s | 504.95s | 85 |
| 2. MAI real-time | **3.34%** | **96.66%** | 0.79s | 0.94s | **504.42s** | 112 |
| 3. MAI post-call VAD utterances | 4.15% | 95.85% | 16.86s turnaround | n/a | 521.03s | 112 |

| Variant | REP WER | CUSTOMER WER |
| --- | --- | --- |
| Azure Speech real-time | 3.69% | 7.42% |
| MAI real-time | 3.14% | **3.82%** |
| MAI post-call | **2.03%** | 6.97% |

The structured result is persisted at
`data/mock-call-stereo-stt-benchmark-results.json`, with one canonical conversation
JSON per engine (`data/mock-call-stereo-conversation-*.json`). MAI real-time still
has the best overall accuracy at real-time latency.

The post-call implementation required an important architectural correction. A raw
MAI enhanced request returns one phrase spanning the full duration of each channel,
despite the general fast-transcription feature table advertising segment timestamps.
Those two channel-sized strings cannot be interleaved into trustworthy turns.
Variant 3 therefore sends the 113 channel-local VAD utterances concurrently after the
call. This preserves measured offsets and valid Conversation PII items, at the cost of
request fan-out and 16.86s turnaround instead of the old 7.8s whole-file turnaround.

### Original mono baseline

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

### Canonical turn output for downstream processing

The benchmark no longer treats the flat transcript as its primary artifact. Every
engine emits one canonical conversation containing chronological
`conversationItems` with:

- stable `id` and `participantId`;
- source `channel`;
- `offset` and `duration` in 100-nanosecond ticks;
- display `text`;
- optional `lexical`, `itn`, `maskedItn`, and word `audioTimings` when supplied.

The flat transcript and WER are derived by joining those ordered turns. Dual-channel
results also include per-participant WER. `conversation_pii_input()` projects the
canonical object into the exact Conversation PII transcript shape by stripping
benchmark-only channel and timing metadata.

This contract follows the documented
[Conversation PII transcript input](https://learn.microsoft.com/en-us/azure/ai-services/language-service/personally-identifiable-information/how-to/redact-conversation-pii):
one asynchronous conversation per request, a list of participant-labelled turns, and
a 1,000-character maximum per conversation item. Architecture 1 projects this object
into the Conversation PII request. Architectures 2 and 3 send the canonical
conversation to DeepSeek as summary context, but the model does not return a
regenerated or redacted conversation.

The downstream capabilities are intentionally asymmetric. Architecture 1 is the
deprecated Azure Language full-redaction baseline and emits transcript entities plus
a redacted conversation. Architectures 2 and 3 are modern DeepSeek summary-only
alternatives and emit a strict PII-safe summary with `redacted: null` and
`entities: []`. Transcript-level PII precision/recall/F1 therefore applies only to
Architecture 1. Summary safety is a separate capability and is not measured by the
transcript-span ground truth.

For dual-channel input, channel identity replaces diarization:

| Channel | Participant |
| --- | --- |
| 0 | REP / agent |
| 1 | CUSTOMER / customer |

Azure Speech uses two concurrent recognizers. MAI Voice Live uses two concurrent
WebSockets with independent VAD. MAI batch uses two concurrent mono requests because
Microsoft's current feature matrix marks stereo channel separation unsupported for
MAI-Transcribe. Results are merged by offset, and overlapping turns remain separate.

Mono remains supported as an un-attributed baseline with one `speaker` participant,
but it is explicitly marked `speakerAttributed: false` and should not feed a
speaker-aware architecture comparison.

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

Billing is on **audio duration**, not wall-clock or request count. The table below is
for the original mono run, where variants 2 and 3 process one identical audio stream.
The 505.8s call is 0.140507 hours.

| Engine | Per call | 1,000 calls | 100,000 calls | Per 1,000 audio hours |
| --- | --- | --- | --- | --- |
| MAI-Transcribe-1.5 (variants 2 and 3) | $0.0506 | $50.58 | $5,058 | $360 |
| Azure Speech standard (variant 1) | $0.1405 | $140.51 | $14,051 | $1,000 |
| *Azure Speech + diarization (not used)* | *$0.1827* | *$182.66* | *$18,266* | *$1,300* |

For stereo, no diarization add-on is needed. MAI nevertheless requires two separately
submitted mono streams because it lacks channel separation. Whether Azure bills that
pattern as one call-hour or two processed audio-hours has not been verified; use 2x as
the conservative planning bound and do not present it as a measured price.

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
only to variant 2.** Variant 1 receives one unbroken stream per channel and the
*service* does its own endpointing; nothing is clipped locally. The local VAD exists
solely to give MAI equivalent utterance boundaries.

Even then, MAI keeps **one persistent WebSocket per channel** open for the whole call -
a commit marks a boundary in that channel's server-side buffer, not a fresh API call
per utterance.

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

The current
[fast-transcription feature matrix](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/fast-transcription-create#feature-availability)
marks both stereo channel separation and word timestamps unsupported for
MAI-Transcribe. The dual-channel implementation therefore uses one mono request or
session per channel and can support text redaction, but not Conversation PII's precise
word-level audio-redaction output.

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

uv run python data/tts.py --stereo
uv run python data/stt.py \
  --audio data/mock-call-stereo.wav \
  --channel-0 REP --channel-1 CUSTOMER
```

**Do not regenerate either fixture casually.** TTS output is not deterministic, so a
new WAV invalidates the corresponding numbers in this document. `--stereo` never
overwrites the mono baseline.

`data/stt.py` exposes
`run_benchmark(audio_path=..., reference_path=..., on_progress=..., channel_map=...)`
for programmatic use, with an `ENGINES` dict and per-engine exception isolation, so
one failing variant does not abort the run. Scoring is skipped when no reference
transcript is supplied.

---

## 8. Open items

- `gpt-live-transcribe` has never been run - the only genuinely incremental option
  remains unmeasured.
- Batch turnaround is a single sample and varies 3.1s to 7.8s with load. No run
  averaging is performed.
- No real call recordings have been tested; all conclusions rest on synthetic audio.
- Entra token refresh is not implemented - see section 6.
- This document's pricing figures cover STT only. Refreshed end-to-end summary-only
  token, latency, and pricing evidence is documented in the README.
