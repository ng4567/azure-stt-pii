This repository prices a migration off the legacy Azure call-processing stack. It
compares the cost, latency, and quality of two end-to-end architectures — speech-to-text,
PII redaction, and summarization — measured on the same recording, and lands both in the
same Fabric analytics estate.

The frontend is the seller-facing front door: a **business case** view that projects the
migration at the customer's own contract discounts and call volume, a **call evidence**
view linking every transcript and the audio back to the files in this repository, and a
**technical details** view with the full measured tables. Both cost views link each
configured unit rate to its Microsoft pricing or model documentation.

# Architecture 1 - Azure Speech + Azure AI Language (current state)

- **STT:** [Azure Speech real-time transcription](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-to-text)
- **PII redaction:** [Conversation PII in Azure AI Language](https://learn.microsoft.com/en-us/azure/ai-services/language-service/personally-identifiable-information/conversation-pii-overview)
- **Summary:** Azure AI Language abstractive/conversation summarization

Conversation PII and summarization run in parallel against independent copies of the
original, unmodified transcript, including PII. This matches the client-confirmed
production flow while keeping downstream latency to the slower endpoint rather than
their sum. After both service calls finish, deterministic passes apply the detected
entities to the transcript and search the generated summary for the same literal
values, replacing matches with typed placeholders such as `[PERSON]` and
`[PHONE_NUMBER]`.

Issue/resolution summarization requires Azure's `Agent` and `Customer` participant
roles. The adapter projects benchmark labels such as `REP` and `CUSTOMER` to those
roles for the summarization request only; source turns and PII input remain unchanged.

# Architecture 2 - MAI real-time + DeepSeek (modernized)

- **STT:** MAI-Transcribe-1.5 through Voice Live, committed at local VAD boundaries
- **Final output:** one strict, PII-safe 80-120 word summary from DeepSeek in Foundry

The DeepSeek endpoint and deployment come from `AZURE_FOUNDRY_ENDPOINT` and
`AZURE_FOUNDRY_DEPLOYMENT`. The complete contents of `data/system_prompt.txt` are
passed unchanged as the LLM system message. DeepSeek receives only compact
participant/text segments, with consecutive turns from the same participant
coalesced; canonical IDs, timing/channel metadata, and local regex candidates are
not sent. It returns exactly `{ "summary": string }`. Deterministic local
post-processing replaces any regex-detected literal that leaks into the summary.
This architecture deliberately does not regenerate or redact the transcript.

A post-call batch variant of Architecture 2 (MAI-Transcribe against VAD-delimited
utterances after the caller hangs up) was benchmarked and then retired: it cost the same
as the real-time path, scored worse on WER, and delivered the transcript 16 seconds
later. Its measured results remain in the `data/*architecture-3*` files as a historical
record; no code path references them.

# Architecture 3 - Azure Communication Services (future)

This option may combine real-time call audio, STT, and PII handling through Azure
Communication Services, followed by an LLM or Azure Language summary. It is not
implemented or included in the current benchmark.

## Common backend output

Every implemented architecture emits the same versioned JSON envelope: architecture
identity and status, original transcript/conversation, nullable redacted output,
summary, entity list, stage metrics, and latency from call start through that
architecture's declared final outputs. Architecture 1 is the Azure Language
full-redaction baseline and returns a redacted conversation plus entities. Its
summarization feature is [scheduled to retire on March 31,
2029](https://learn.microsoft.com/en-us/azure/ai-services/language-service/summarization/overview);
the benchmark does not claim that every Azure Language capability retires on that date.
Architecture 2 is the modern LLM alternative and intentionally returns a
PII-safe summary only, with `redacted: null` and `entities: []`. One failed
architecture is reported independently and does not discard its peer.

## Backend service configuration

Local development loads an ignored repository-root `.env`; deployed environments
should inject the same names directly. Authentication remains Microsoft Entra ID via
`DefaultAzureCredential`--no service keys are stored in this repository.

```dotenv
AZURE_LANGUAGE_ENDPOINT=<endpoint>
AZURE_FOUNDRY_ENDPOINT=<endpoint>
AZURE_FOUNDRY_DEPLOYMENT=<deepseek-deployment-name>
AZURE_SPEECH_RESOURCE_NAME=<speech-resource>
AZURE_SPEECH_ENDPOINT=<endpoint>
AZURE_SPEECH_RESOURCE_ID=/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CognitiveServices/accounts/<speech-resource>
AZURE_VOICE_LIVE_URL=<endpoint>
# Optional overrides:
AZURE_LANGUAGE_API_VERSION=2024-11-01
AZURE_FOUNDRY_API_VERSION=2025-04-01-preview
AZURE_FOUNDRY_MAX_ATTEMPTS=2
SYSTEM_PROMPT_PATH=data/system_prompt.txt
AZURE_REQUEST_TIMEOUT_SECONDS=300
```

`AZURE_LANGUAGE_ENDPOINT` must be the Language resource's Cognitive Services
data-plane `<endpoint>`. A Foundry project endpoint is a project-management endpoint
and does not expose the Conversation Analysis job route used by Architecture 1.

`data/system_prompt.txt` is runtime configuration and is copied into the backend
container. Its entire content is used verbatim as the DeepSeek system message.

## Foundry infrastructure

[`infra/main.bicep`](infra/main.bicep) creates `<resource-group>` in East
US, provisions a Microsoft Foundry resource, and deploys DeepSeek-V4 Flash using the
Global Standard SKU. The deployment keeps local key authentication disabled.

Deploy it with:

```bash
./infra/deploy.sh
```

The wrapper asserts that Azure CLI is installed and logged in before starting the
subscription deployment. Use `--parameters foundryName=<globally-unique-name>` to
override the generated Foundry resource name. The deployment outputs map directly to
`AZURE_FOUNDRY_ENDPOINT` and `AZURE_FOUNDRY_DEPLOYMENT`.

## Customer Oracle-to-OneLake deployment

[`infra/customer/`](infra/customer/README.md) contains a reproducible deployment for
the downstream analytics architecture: PII-safe call analytics land in the customer's
existing Oracle database, replicate into OneLake through Fabric Mirroring, and are
queried in natural language by a Fabric Data Agent. It includes the Fabric capacity
template with pre-flight checks, an Oracle 19c schema with the Mirroring
prerequisites, a deterministic 100-call PII-safe corpus generator, and a determinism
harness that scores the agent's answers against a hand-written SQL baseline.

---

# STT Benchmark

The `data/` folder contains a reproducible speech-to-text benchmark used to compare
the STT stage of each architecture on accuracy and latency.

## Scripts

| Script | Purpose |
| --- | --- |
| `data/tts.py` | Synthesizes `data/mock-call-transcript.txt` with **MAI Voice 2**. The default preserves the mono baseline; `--stereo` writes `mock-call-stereo.wav` with REP on channel 0 and CUSTOMER on channel 1. |
| `data/stt.py` | Runs all STT pipelines, emits timestamped speaker turns plus a derived flat transcript, and scores each against the reference. |

Run the mono baseline with `uv run python data/tts.py` and
`uv run python data/stt.py`. Generate and benchmark the turn-ready fixture with:

```bash
uv run python data/tts.py --stereo
uv run python data/stt.py \
  --audio data/mock-call-stereo.wav \
  --channel-0 REP --channel-1 CUSTOMER
```
Both authenticate with Microsoft Entra ID (`DefaultAzureCredential`) because local
key auth is disabled on the Speech resource.

## PII-ready, dual-channel transcript boundary

The primary STT output is now an ordered conversation, not a flat string. For a
dual-channel call, channel identity is authoritative speaker identity - no diarization
is needed or billed. The upload form defaults channel 0 to `REP` and channel 1 to
`CUSTOMER`, and stores that mapping with the recording.

```json
{
  "id": "call-architecture-1",
  "language": "en",
  "modality": "transcript",
  "speakerAttributed": true,
  "channelMap": {"0": "REP", "1": "CUSTOMER"},
  "conversationItems": [
    {
      "id": "turn-0001",
      "participantId": "REP",
      "channel": 0,
      "offset": 11700000,
      "duration": 5200000,
      "text": "Good morning.",
      "lexical": "good morning",
      "itn": "good morning",
      "maskedItn": "good morning",
      "audioTimings": [
        {"word": "good", "offset": 11700000, "duration": 2100000}
      ]
    }
  ]
}
```

Offsets and durations use 100-nanosecond ticks, matching Azure Speech and Conversation
PII. `channel`, `offset`, `duration`, `speakerAttributed`, and `channelMap` are
benchmark metadata; `conversation_pii_input()` strips them when constructing a future
Conversation PII request. Each item is validated against that API's 1,000-character
limit. See Microsoft's
[Conversation PII overview](https://learn.microsoft.com/en-us/azure/ai-services/language-service/personally-identifiable-information/conversation-pii-overview)
and [transcript request example](https://learn.microsoft.com/en-us/azure/ai-services/language-service/personally-identifiable-information/how-to/redact-conversation-pii).

The engines reach this common contract differently:

- **Azure Speech real-time** runs one recognizer per channel concurrently and provides
  detailed lexical/ITN forms and word timestamps.
- **MAI Voice Live** runs one WebSocket and independent VAD stream per channel.
  MAI returns segment text, not word timestamps.

Turns from both channels are merged by audio offset. Overlapping speech remains two
overlapping turns; it is never forced into an invented serial order. The old
`transcript` string remains as a derived compatibility/scoring view. Mono uploads
remain supported but have one `speaker` participant and are marked
`speakerAttributed: false`, so they are not suitable for speaker-aware downstream
comparison.

## Authentication, and running this in Azure Container Apps

Yes - containerizing this and running it in ACA works, and is actually the cleaner
deployment. `DefaultAzureCredential` walks a chain of credential sources; locally it
lands on your Azure CLI login, and inside ACA it lands on `ManagedIdentityCredential`.
No code changes are needed. But it is not automatic - three things must be true:

1. **Enable a managed identity on the Container App.** System-assigned is simplest
   (`az containerapp identity assign --system-assigned`). If you use a *user-assigned*
   identity instead, you must also set the `AZURE_CLIENT_ID` environment variable on
   the container to that identity's client ID, otherwise `DefaultAzureCredential`
   cannot tell which identity to request a token for and fails at runtime.
2. **Grant that identity a data-plane role on the Speech resource.**
   `Cognitive Services Speech User` is the correct least-privilege role and covers both
   variants - its data actions include `SpeechServices/*/transcriptions/*` and
   `SpeechServices/voicelive/realtime/*` (Voice Live).
   `Cognitive Services User` also works but grants `Microsoft.CognitiveServices/*`.

   ```bash
   az role assignment create \
     --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
     --role "Cognitive Services Speech User" \
     --scope "/subscriptions/$SUB/resourceGroups/<resource-group>/providers/Microsoft.CognitiveServices/accounts/<speech-resource>"
   ```

   This step is easy to miss locally, because the current developer identity reaches
   the resource through subscription-scope `Owner` and resource-group-scope Azure AI
   roles rather than an explicit assignment on the account. A managed identity starts
   with nothing and inherits none of that.
3. **Keep egress open** to `*.cognitiveservices.azure.com` and
   `*.services.ai.azure.com`, including **outbound WebSocket** for Voice Live. If the
   Container App environment sits in a locked-down VNet, the real-time variant is the
   first thing that breaks.

Because `disableLocalAuth: true` is set on the resource, managed identity is not
merely the recommended path - it is the *only* one. There is no key fallback.

Two caveats for production, both irrelevant to a ~9 minute benchmark run but not to a
long-lived service:

- **Tokens are fetched once at startup and never refreshed.** Entra tokens expire in
  roughly an hour, so a long-running container or a call lasting over an hour will
  see auth failures mid-stream. The Speech SDK exposes `authorization_token` for
  in-flight refresh; the Voice Live WebSocket needs a reconnect.
- Voice Live requires a `model=gpt-4.1` parameter even for transcription-only use. If
  that inference path is gated separately in your tenant, the identity may also need
  `Cognitive Services OpenAI User`.


## Results

### Dual-channel, turn-ready run

504.2s stereo fixture, 989-word reference, 113 channel-local VAD utterances:

| STT variant | Mode | WER | Accuracy | Mean lag | p95 lag | Transcript ready | Turns |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1. Azure Speech real-time | 2 incremental channel sessions | 5.36% | 94.64% | **0.76s** | **0.97s** | 504.72s | 85 |
| 2. MAI real-time | 2 Voice Live channel sessions | **3.34%** | **96.66%** | 0.82s | 0.97s | **504.48s** | 112 |

Per-participant WER:

| Variant | REP | CUSTOMER |
| --- | --- | --- |
| Azure Speech real-time | **3.69%** | 7.42% |
| MAI real-time | 3.14% | **3.82%** |

Note that MAI's mean finalization lag is 0.06s *worse* than the Speech SDK's. Both
engines land the transcript within about a second of the caller hanging up; the
migration case rests on accuracy and cost, not on streaming latency.

The cached result was refreshed through the supported asynchronous web/API benchmark
on August 6, 2026. Architecture 2 completed with `redacted: null`, `entities: []`, and
a summary-only DeepSeek response.

| Architecture | STT ready | Downstream | End to end |
| --- | ---: | ---: | ---: |
| 1. Azure Speech + Azure Language | 504.72s | 4.58s | 509.30s |
| 2. MAI real-time + DeepSeek | **504.48s** | **3.68s** | **508.16s** |

End to end stops when each architecture's declared final outputs are ready: full
redaction plus summary for Architecture 1, and the PII-safe summary for Architecture 2.
Architecture 1 overlaps the Conversation PII and summarizer endpoints, so their
individual stage times are not added together. The 1.14s end-to-end difference is
within noise on an 8-minute call; the meaningful gap is the 20% less work after the
transcript exists.

DeepSeek stage output separates regex detection, prompt/token preparation,
the Foundry HTTP call, strict summary validation, summary sanitization, and residual
backend orchestration. The compact participant/text projection coalesced 112 source
turns into 56 segments and serialized to about 8 KB for each DeepSeek request.

### Original mono baseline

The table below is the original 505s synthetic support call and
989-word reference transcript. Both pipelines run concurrently, so a full pass costs
about one call duration.

These STT variants map directly to the two end-to-end architectures above: Azure
Speech real-time and MAI real-time. Azure Communication Services is Architecture 3 and
is not part of this benchmark.

| STT variant | Mode | WER | Accuracy | Mean lag | p95 lag | Transcript ready |
| --- | --- | --- | --- | --- | --- | --- |
| 1. Azure Speech real-time (SDK) | real-time, incremental | 5.66% | 94.34% | 0.77s | 1.04s | 506.0s |
| 2. MAI-Transcribe-1.5 real-time (Voice Live) | real-time, utterance micro-batch | **2.93%** | **97.07%** | 0.80s | 0.91s | **505.9s** |

MAI-Transcribe-1.5 is roughly **half the word error rate** of the classic Speech SDK
recognizer at equivalent streaming latency.

**WER** (word error rate) is the standard STT accuracy metric: the hypothesis is
aligned to the reference with minimum edit distance, and
`WER = (substitutions + deletions + insertions) / reference_word_count`. Lower is
better, and `accuracy = 1 - WER`.

Full findings, error breakdowns, and API gotchas: [`docs/stt-results.md`](docs/stt-results.md).


## Pricing

Costs are computed from list rates and three discounts the seller enters in the app —
one for Azure AI Speech, one for Azure AI Language, and one for the Foundry model. All
three default to 0% (list price). The "applied rate" column below shows one worked
example: **90% off Speech** (including MAI-Transcribe, which bills against the same
contract line), **70% off Azure Language**, and no discount on the Foundry model.

The Foundry discount exists to approximate a provisioned-throughput (PTU) deployment.
PTU is billed as reserved hourly capacity rather than per token, so an effective
discount at the expected utilization is the closest per-call equivalent.

| Component | List rate | Applied rate (worked example) | Source |
| --- | --- | --- | --- |
| Azure Speech standard STT | $1.00 / submitted audio hour | $0.10 / hour | [Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/) |
| MAI-Transcribe-1.5 | $0.36 / submitted audio hour | $0.036 / hour | [Model catalog](https://ai.azure.com/catalog/models/MAI-Transcribe-1.5) |
| Conversation PII, first 0.5M records | $1.00 / 1,000 text records | $0.30 / 1,000 | [Language pricing](https://azure.microsoft.com/en-us/pricing/details/language/) |
| Conversation summarization, Standard | $2.00 / 1,000 text records | $0.60 / 1,000 | [Language pricing](https://azure.microsoft.com/en-us/pricing/details/language/) |
| DeepSeek-V4 Flash Global input | $0.19 / 1M tokens | $0.19 / 1M | [DeepSeek pricing](https://azure.microsoft.com/en-us/pricing/details/ai-foundry-models/deepseek/) |
| DeepSeek-V4 Flash Global output | $0.51 / 1M tokens | $0.51 / 1M | [DeepSeek pricing](https://azure.microsoft.com/en-us/pricing/details/ai-foundry-models/deepseek/) |

A text record is each started block of 1,000 characters. Conversation PII volume
tiers are $1.00/1K for 0-0.5M records, $0.75 for 0.5-2.5M, $0.30 for 2.5-10M, and
$0.25 above 10M. Per-call figures use the first tier; the app's monthly and annual
projections walk the ladder, which materially *reduces* the Architecture 1 cost at
volume. Summarization projections compare Standard pay-as-you-go with the 3M
($3,300/month plus $1.10/1K overage) and 10M ($7,000/month plus $0.70/1K overage)
commitments, then use the cheapest valid plan for the projected monthly volume.
Commitment fees are never treated as per-call rates.

The 504.168-second stereo call submits two mono channels, or 0.280093 billable audio
hours. Architecture 1 sends 5,212 characters (6 records) to PII and 5,830
input-plus-output characters (6 records) to summarization. The checked-in compact-input
run measured 2,361 input / 144 output tokens for Architecture 2.

| Architecture | List / call | Discounted / call | 1,000 calls | 100,000 calls |
| --- | ---: | ---: | ---: | ---: |
| 1. Azure Speech + Azure Language | $0.298093 | **$0.033409** | $33.41 | $3,333.43* |
| 2. MAI real-time + DeepSeek | $0.101356 | **$0.010605** | $10.61 | $1,060.54 |

\*The 100,000-call Architecture 1 projection applies the supplied PII tiers across
600,000 records and Standard summarization to another 600,000 records before the
Azure Language discount. The per-call and 1,000-call figures remain in PII tier 1.

Architecture 2 is the cheaper pipeline by about **66% at list price** and 68.2% at
the worked-example discounts — $2,273 a month at 100,000 calls, or $27,275 a year.
The frontend recomputes all of this for every new run from submitted audio duration,
Azure Language characters, and model usage returned across all retry attempts.
Price is shown first in each comparison. The lowest cost, latency, and WER values and
the highest accuracy scores are highlighted per column; those winners are recomputed
from every newly uploaded recording rather than being fixed to the built-in call.
Per-participant WER is available in a collapsed detail view.

These totals exclude hosting, storage, logging, network egress, fixture TTS generation,
Fabric capacity, and any separate Voice Live host-model charge. Diarization is disabled. Audio duration
is conservatively multiplied by channel count because both channels are independently
submitted.

## PII capabilities and scoring

The built-in call includes 26 fictional PII mentions in
`data/mock-call-pii-ground-truth.json`. Annotations use character offsets in the
flattened reference text produced by `data/stt.py:reference_text()` and include a
SHA-256 digest so edits to the transcript cannot silently invalidate the spans. Custom
runs can upload the same JSON format alongside a reference transcript.

Because each STT engine creates different turn boundaries and recognition errors, the
scorer monotonically aligns normalized reference words to each architecture's source
conversation before projecting annotations into its turn IDs and offsets. Entities
whose words are deleted, substituted, split across turns, or otherwise cannot be
projected are reported through the **alignment rate** and are not counted as redaction
false negatives. This separates STT loss from downstream PII-redaction loss.

Projected entities are compared with provider entities using exact source-turn
spans. The UI reports entity **precision**, **recall**, and **F1**; **category
accuracy** among matched spans after normalizing provider category aliases; and **PII
leakage rate**, equal to unmatched projected ground-truth entities divided by all
projected ground-truth entities. It also displays alignment and TP/FP/FN counts.

Transcript-level PII scoring applies only to architectures that emit transcript
redaction/entity output. Architecture 1 remains scoreable. Architecture 2 returns
`redacted: null` and `entities: []`, so it is omitted from transcript PII
precision/recall/F1 rather than counted as a failure. Its capability is different:
it produces a strict PII-safe summary, guarded by typed-placeholder instructions and
deterministic replacement of locally detected literals. Summary safety is not
equivalent to transcript-redaction recall, and the current ground truth does not
define a summary-safety score. The refreshed cached report therefore contains
`pii_accuracy` only for Architecture 1.

# Running it

The app opens on the **business case** view, which reads the checked-in result and
needs no Azure access. **Call evidence** links the recording, the reference script, and
both stacks' transcripts back to the files in `data/`. **Technical details** holds the
measured tables and the run history; `#business`, `#evidence`, and `#technical`
deep-link to each.

### Built-in call, or your own

Every figure in the app comes from one selected recording, named in the bar at the top
of the page. That is the built-in sample call until you run your own, and the business
case says so in as many words — a price quoted from the wrong call is worse than no
price at all.

To use a customer's recording, open **Use the built-in call, or your own** on the
business case and attach it. Audio is the only required input:

| Input | Required | What it buys |
| --- | --- | --- |
| Call audio | yes | Cost, latency, and both transcripts. Stereo with the rep on one channel and the customer on the other gives speaker identity without billing for diarization. |
| Reference transcript | no | Word error rate. Without it, accuracy is not scored and the UI says so. |
| PII ground truth | no | Transcript-level PII precision, recall, and F1. Requires a reference transcript. |

Both architectures then run concurrently against that audio. When the run finishes the
app switches to it automatically, and every cost, latency, accuracy, and transcript
figure — on all three views — is recomputed from that call. The recording picker
switches back to the built-in sample, or between runs, at any time.

"Re-run the built-in call" runs the shipped fixture live against Azure instead of
reading the saved result. Word error rate is scored only when a reference is present;
latency and transcripts are produced either way.

The UI immediately displays the checked-in results for `data/mock-call-stereo.wav`
without making Azure requests. The original mono fixture remains the CLI baseline and
is not presented as a competing frontend benchmark input.

The architecture diagrams under `frontend/public/architecture/` are generated — edit
`build_diagrams.py` there and re-run it, so the shared Fabric analytics tail stays
identical on both pages.

```bash
docker compose up --build              # UI on http://localhost:3000
cd frontend && bun install && bun dev   # frontend-only development
```

The backend container authenticates to Azure with `DefaultAzureCredential`, reusing
the host's `az login` profile via a mounted `~/.azure`, so run `az login` first.
Uploaded audio in any format ffmpeg can read is normalized to 16-bit mono/stereo PCM
without collapsing channel identity; compatible PCM is passed through untouched.

Real-time engines stream the recording at 1x, so a run takes roughly as long as the
call itself. Runs are started, polled, and read back asynchronously.

The original CLI still benchmarks the checked-in mock call directly:

```bash
uv run python data/stt.py
```
