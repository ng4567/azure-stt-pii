The point of this repo is to give cost comparison between two Architectures for transcribing Speech to Text and then summarizing the summary and redacting the transcript. We will benchmark both total cost and latency.


# Architecture 1 - Current State

- STT Model: [Azure Speech Transcriber Real Time](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-to-text)
- PII Redactor: [Conversation PII redaction in Azure AI Language](https://learn.microsoft.com/en-us/azure/ai-services/language-service/personally-identifiable-information/conversation-pii-overview)
- Summarizer: [Azure Language Abstractive Summarization](https://learn.microsoft.com/en-us/azure/ai-services/language-service/summarization/how-to/summarize?tabs=text-summarization#try-text-abstractive-summarization)

# Architecture 2 - Modernized Version

This architecture will attempt to beat the performance and cost of the original redactor by using 1 LLM with a combination of regex and the LLM's system prompt to have the PII redacted and the summarization in 1 step. It also uses a newer MAI model compared to the old Azure Speech Transcriber

STT: MAI-Transcribe-1.5
Summarizer: Foundry LLM
PII Redactor: Regex + LLM

# Architecture 3 - Azure Communication Services (Potentially Explore Later but not now)

STT + PII all in one directly from the call real time
Summarizer with LLM or Azure Summarizer

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

Note: these are STT *variants*, numbered independently of the architecture sections
above. Variants 1 and 2 are the STT stages of Architecture 1 and Architecture 2;
variant 3 is Architecture 2's model in post-call batch mode, and is unrelated to the
Azure Communication Services architecture.

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

List price, eastus, billed on **audio duration** rather than wall-clock or request
count. The figures below apply to the original mono run, where variants 2 and 3
process the same single audio stream.

| Engine | Rate | Source |
| --- | --- | --- |
| MAI-Transcribe-1.5 | $0.36 / audio hour | [Model catalog](https://ai.azure.com/catalog/models/MAI-Transcribe-1.5) |
| Azure Speech, standard STT | $1.00 / audio hour | [Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/) |
| Azure Speech, diarization add-on | +$0.30 / audio hour | [Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/) |

**Diarization is not enabled in any benchmarked variant** and is excluded below.
Variant 1 uses `SpeechRecognizer`, not `ConversationTranscriber`, and neither MAI path
sets a speaker-separation option; speaker attribution here comes from the transcript's
own `REP:` / `CUSTOMER:` labels. If a variant later needs service-side diarization, add
$0.30/hr, taking Azure Speech to $1.30/hr and widening the gap to 3.6x.

The dual-channel pipeline also avoids diarization, but MAI requires two independently
submitted mono streams. Do not double or reuse the mono price until Azure billing for
that exact request pattern is verified; the conservative upper bound is 2x.

The 505.8s benchmark call is 0.140507 audio hours.

| Engine | Per call | 1,000 calls | 100,000 calls | Per 1,000 audio hours |
| --- | --- | --- | --- | --- |
| MAI-Transcribe-1.5 (variants 2 and 3) | $0.0506 | $50.58 | $5,058 | $360 |
| Azure Speech standard (variant 1) | $0.1405 | $140.51 | $14,051 | $1,000 |
| *Azure Speech + diarization (not used)* | *$0.1827* | *$182.66* | *$18,266* | *$1,300* |

MAI-Transcribe-1.5 is **2.78x cheaper** than standard Azure Speech STT while scoring
roughly half the WER - it wins on cost and accuracy simultaneously on this workload.
At 100k calls that is about **$8,993** saved.

Excludes the LLM cost of Voice Live's mandatory `model=gpt-4.1` parameter, TTS costs
for generating the test audio, and the downstream PII-redaction and summarization
stages. List price only, with no commitment tiers or negotiated discounts.

# Running it

The benchmark is driven from one action in the web UI. Upload a call recording and,
optionally, a reference transcript, then all three architectures run concurrently.
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
