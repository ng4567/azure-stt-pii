This repository compares the cost, latency, and quality of end-to-end call-processing
architectures: speech-to-text, PII redaction, and summarization. Three architectures
are implemented and benchmarked today; a fourth is reserved for future exploration.

# Architecture 1 - Azure Speech + Azure Language

- **STT:** [Azure Speech real-time transcription](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-to-text)
- **PII redaction:** [Conversation PII in Azure AI Language](https://learn.microsoft.com/en-us/azure/ai-services/language-service/personally-identifiable-information/conversation-pii-overview)
- **Summary:** Azure AI Language abstractive/conversation summarization

Conversation PII and summarization both receive the original, unmodified transcript,
including PII. This matches the client-confirmed production flow. After both service
calls finish, a deterministic sanitization pass checks every PII entity identified by
Conversation PII against the generated summary and replaces matches with typed
placeholders such as `[PERSON]` and `[PHONE_NUMBER]`.

Issue/resolution summarization requires Azure's `Agent` and `Customer` participant
roles. The adapter projects benchmark labels such as `REP` and `CUSTOMER` to those
roles for the summarization request only; source turns and PII input remain unchanged.

# Architecture 2 - MAI real-time + DeepSeek

- **STT:** MAI-Transcribe-1.5 through Voice Live, committed at local VAD boundaries
- **PII redaction + summary:** one structured-output DeepSeek call hosted in Foundry,
  assisted by deterministic regex candidates

The DeepSeek endpoint and deployment come from `AZURE_FOUNDRY_ENDPOINT` and
`AZURE_FOUNDRY_DEPLOYMENT`. The complete contents of `data/system_prompt.txt` are
passed unchanged as the LLM system message. The model returns a redacted canonical
conversation and summary in one response; the same deterministic summary sanitizer
provides defense in depth.

# Architecture 3 - MAI batch + DeepSeek

Architecture 3 uses the same DeepSeek redaction/summarization stage and prompt as
Architecture 2, but runs MAI-Transcribe-1.5 after the call against VAD-delimited
utterances. Keeping it separate makes the latency/cost tradeoff between real-time and
post-call STT explicit while preserving an identical downstream output contract.

# Architecture 4 - Azure Communication Services (future)

This option may combine real-time call audio, STT, and PII handling through Azure
Communication Services, followed by an LLM or Azure Language summary. It is not
implemented or included in the current three-way benchmark.

## Common backend output

Every implemented architecture emits the same versioned JSON shape: architecture
identity and status, original transcript/conversation, redacted
transcript/conversation, sanitized summary, normalized PII entities, and metrics for
the `stt`, `pii_redaction`, `summarization`, and `summary_sanitization` stages. One
failed architecture is reported independently and does not discard successful peers.

## Backend service configuration

Local development loads an ignored repository-root `.env`; deployed environments
should inject the same names directly. Authentication remains Microsoft Entra ID via
`DefaultAzureCredential`--no service keys are stored in this repository.

```dotenv
AZURE_LANGUAGE_ENDPOINT=https://<language-resource>.cognitiveservices.azure.com
AZURE_FOUNDRY_ENDPOINT=https://<foundry-resource>.cognitiveservices.azure.com
AZURE_FOUNDRY_DEPLOYMENT=<deepseek-deployment-name>
# Optional overrides:
AZURE_LANGUAGE_API_VERSION=2024-11-01
AZURE_FOUNDRY_API_VERSION=2025-04-01-preview
AZURE_FOUNDRY_MAX_ATTEMPTS=2
SYSTEM_PROMPT_PATH=data/system_prompt.txt
AZURE_REQUEST_TIMEOUT_SECONDS=300
```

`AZURE_LANGUAGE_ENDPOINT` must be the Language resource's Cognitive Services
data-plane endpoint (for example,
`https://finance-app-resource.cognitiveservices.azure.com`). A Foundry project URL
such as `https://<resource>.services.ai.azure.com/api/projects/<project>` is a project
management endpoint and does not expose the Conversation Analysis job route used by
Architecture 1.

`data/system_prompt.txt` is runtime configuration and is copied into the backend
container. Its entire content is used verbatim as the DeepSeek system message.

## Foundry infrastructure

[`infra/main.bicep`](infra/main.bicep) creates the `stt-pii` resource group in East
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
- **MAI batch** runs one mono request per channel concurrently because
  MAI-Transcribe-1.5 does not support stereo channel separation.

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
   `Cognitive Services Speech User` is the correct least-privilege role and covers all
   three variants - its data actions include `SpeechServices/*/transcriptions/*` (fast
   transcription) and `SpeechServices/voicelive/realtime/*` (Voice Live).
   `Cognitive Services User` also works but grants `Microsoft.CognitiveServices/*`.

   ```bash
   az role assignment create \
     --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
     --role "Cognitive Services Speech User" \
     --scope "/subscriptions/$SUB/resourceGroups/rg-charter-stt-pii/providers/Microsoft.CognitiveServices/accounts/charter-stt-pii-resource"
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
| 1. Azure Speech real-time | 2 incremental channel sessions | 5.36% | 94.64% | 0.76s | 0.92s | 504.3s | 85 |
| 2. MAI real-time | 2 Voice Live channel sessions | **3.34%** | **96.66%** | 0.83s | 1.02s | **504.4s** | 112 |
| 3. MAI post-call | 113 concurrent VAD-utterance requests | 4.15% | 95.85% | 17.75s turnaround | n/a | 521.9s | 112 |

Per-participant WER:

| Variant | REP | CUSTOMER |
| --- | --- | --- |
| Azure Speech real-time | 3.69% | 7.42% |
| MAI real-time | 3.14% | **3.82%** |
| MAI post-call | **2.03%** | 6.97% |

MAI's whole-file enhanced response exposes one full-duration phrase per channel, which
cannot be converted into chronologically interleaved turns. Variant 3 therefore
submits the already-detected post-call VAD utterances concurrently. It remains
post-call and preserves real offsets, but request fan-out raises turnaround from the
old 7.8s whole-file result to 17.75s.

### Original mono baseline

The table below is the original 505s synthetic support call and
989-word reference transcript. All three pipelines run
concurrently, so a full pass costs about one call duration.

These STT variants map directly to the first three end-to-end architectures above:
Azure Speech real-time, MAI real-time, and MAI batch. Azure Communication Services is
Architecture 4 and is not part of this benchmark.

| STT variant | Mode | WER | Accuracy | Mean lag | p95 lag | Transcript ready |
| --- | --- | --- | --- | --- | --- | --- |
| 1. Azure Speech real-time (SDK) | real-time, incremental | 5.66% | 94.34% | 0.77s | 1.04s | 506.0s |
| 2. MAI-Transcribe-1.5 real-time (Voice Live) | real-time, utterance micro-batch | **2.93%** | **97.07%** | 0.80s | 0.91s | **505.9s** |
| 3. MAI-Transcribe-1.5 batch (one call) | post-call | 4.04% | 95.96% | 7.8s turnaround | n/a | 513.6s |

MAI-Transcribe-1.5 is roughly **half the word error rate** of the classic Speech SDK
recognizer at equivalent streaming latency, and is more accurate when fed utterances
than when handed the whole call at once.

**WER** (word error rate) is the standard STT accuracy metric: the hypothesis is
aligned to the reference with minimum edit distance, and
`WER = (substitutions + deletions + insertions) / reference_word_count`. Lower is
better, and `accuracy = 1 - WER`.

Full findings, error breakdowns, and API gotchas: [`docs/stt-results.md`](docs/stt-results.md).


## Pricing

The comparison uses public list rates and the customer's contractual discounts:
**90% off Speech** (including MAI-Transcribe), **70% off Azure Language**, and no
discount on Foundry-hosted DeepSeek.

| Component | List rate | Applied rate | Source |
| --- | --- | --- | --- |
| Azure Speech standard STT | $1.00 / submitted audio hour | $0.10 / hour | [Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/) |
| MAI-Transcribe-1.5 | $0.36 / submitted audio hour | $0.036 / hour | [Model catalog](https://ai.azure.com/catalog/models/MAI-Transcribe-1.5) |
| Conversation PII, first 0.5M records | $1.00 / 1,000 text records | $0.30 / 1,000 | [Language pricing](https://azure.microsoft.com/en-us/pricing/details/language/) |
| Conversation summarization | $7,000 / 10M text records | $2,100 / 10M | [Language pricing](https://azure.microsoft.com/en-us/pricing/details/language/) |
| DeepSeek-V4 Flash Global input | $0.19 / 1M tokens | $0.19 / 1M | [DeepSeek pricing](https://azure.microsoft.com/en-us/pricing/details/ai-foundry-models/deepseek/) |
| DeepSeek-V4 Flash Global cached input | $0.028 / 1M tokens | $0.028 / 1M | [DeepSeek pricing](https://azure.microsoft.com/en-us/pricing/details/ai-foundry-models/deepseek/) |
| DeepSeek-V4 Flash Global output | $0.51 / 1M tokens | $0.51 / 1M | [DeepSeek pricing](https://azure.microsoft.com/en-us/pricing/details/ai-foundry-models/deepseek/) |

A text record is each started block of 1,000 characters. Conversation PII volume
tiers are $1.00/1K for 0-0.5M records, $0.75 for 0.5-2.5M, $0.30 for 2.5-10M, and
$0.25 above 10M; this benchmark uses the first tier. The 504.168-second stereo call
submits two mono channels, or 0.280093 billable audio hours. Architecture 1 sends
5,212 characters (6 records) to PII and 5,872 input-plus-output characters (6 records)
to summarization. Measured DeepSeek usage is 6,906 input / 6,743 output tokens for
Architecture 2 and 7,124 input / 6,816 output tokens for Architecture 3. No cached
input was reported, so the cached-token rate contributes $0.

| Architecture | List / call | Discounted / call | 1,000 calls | 100,000 calls |
| --- | ---: | ---: | ---: | ---: |
| 1. Azure Speech + Azure Language | $0.290293 | **$0.031069** | $31.07 | $3,099.43* |
| 2. MAI real-time + DeepSeek | $0.105585 | **$0.014834** | $14.83 | $1,483.44 |
| 3. MAI batch + DeepSeek | $0.105663 | **$0.014913** | $14.91 | $1,491.31 |

\*The 100,000-call Architecture 1 projection applies the supplied PII tiers across
600,000 records: 500,000 records at $1.00/1K and 100,000 at $0.75/1K before the
70% Azure Language discount. The per-call and 1,000-call figures remain in tier 1.

With discounts, Architecture 2 is the cheapest measured pipeline: about **52.3% less
than Architecture 1** per benchmark call and $1,615.99 less per 100,000 calls after
applying the PII volume tier.
Architecture 3 is only $0.000079 per call more than Architecture 2 because its measured
DeepSeek response is slightly larger. The frontend calculates the same list and
discounted totals for every new run from submitted audio duration, Azure Language
characters, and DeepSeek usage returned across all retry attempts.

These totals exclude hosting, storage, logging, network egress, fixture TTS generation,
and any separate Voice Live host-model charge. Diarization is disabled. Audio duration
is conservatively multiplied by channel count because both channels are independently
submitted.

## PII redaction accuracy

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

Projected entities are compared with provider/model entities using exact source-turn
spans. The UI reports entity **precision**, **recall**, and **F1**; **category
accuracy** among matched spans after normalizing provider category aliases; and **PII
leakage rate**, equal to unmatched projected ground-truth entities divided by all
projected ground-truth entities. It also displays alignment and TP/FP/FN counts. The
historical cached report predates downstream entity output, so it correctly displays
PII accuracy as not scored rather than inventing metrics; annotated live runs include
`pii_accuracy` in the benchmark report.

# Running it

The benchmark is driven from one action in the web UI. Upload a call recording and,
optionally, a reference transcript, then all three implemented architectures run
concurrently.
With no files selected, the action uses the built-in turn-ready mock call and its
reference. A transcript-only submission uses the built-in audio with that transcript
as the scoring reference. Word error rate is scored only when a reference is present;
latency and transcripts are produced either way.

The UI immediately displays the checked-in results for `data/mock-call-stereo.wav`
without making Azure requests. The original mono fixture remains the CLI baseline and
is not presented as a competing frontend benchmark input. The cached comparison links
to its default audio and reference transcript so either fixture can be downloaded
directly from the frontend.

```bash
docker compose up --build              # backend API on http://localhost:8000
cd frontend && bun install && bun dev   # UI on http://localhost:3000
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
