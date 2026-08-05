## Methodology notes

These caveats matter when interpreting the table.

- **MAI-Transcribe has no true incremental streaming path.** It never emits
  `input_audio_transcription.delta`, and Voice Live's own VAD returns a transcript
  only for the *first* turn of a session (verified against server VAD, semantic VAD,
  `create_response: true`, and manual `response.create`). It must be driven with
  explicit `input_audio_buffer.commit` calls. Architecture 1, by contrast, streams
  partial hypotheses while the speaker is still talking - a real capability
  difference that the lag column does not capture.
- **Chunking is VAD-aligned, not clock-aligned.** `stt.py` runs a local VAD and
  commits at natural pauses, so both real-time engines see equivalent utterance
  boundaries. This matters a lot - committing on a fixed clock slices sentences
  mid-phrase and starves the model of context:

  | Fixed commit interval | WER | Worst-case word delay |
  | --- | --- | --- |
  | 3s | 11.11% | ~3.9s |
  | 5s | 5.15% | ~6.0s |
  | 10s | 2.97% | ~10.9s |
  | 20s | 2.60% | ~21.1s |
  | 30s | 2.60% | ~31.1s |

- **Latency is finalization lag**: the delay between the end of an utterance's audio
  and the arrival of its final transcript, measured identically on both real-time
  paths. Architecture 3 has no per-utterance notion, so it is reported as whole-call
  turnaround, and its "transcript ready" figure includes the call duration it must
  wait through first.
- **Chunking is VAD-aligned only for MAI.** Architecture 1 receives one unbroken
  stream and the *service* does its own endpointing; nothing is clipped locally. The
  local VAD exists solely to give MAI equivalent utterance boundaries. Even then MAI
  keeps a single persistent WebSocket open - a commit marks a boundary in the
  server-side buffer, it is not a fresh API call per utterance.
- **Audio is streamed at 1x.** A file-based `AudioConfig` lets the Speech SDK consume
  audio about twice as fast as real time, which makes latency meaningless, so the SDK
  is fed through a throttled `PullAudioInputStream`.
- **Scoring folds numbers to a canonical form.** The engines apply different inverse
  text normalization ("three hundred" vs "300", "eleventh" vs "11th"). Without
  folding, formatting differences alone inflated WER by roughly 2 points.
- **Synthetic audio flatters every engine.** There is no overlapping speech,
  crosstalk, or line noise, so absolute WER and lag will be better here than on real
  call recordings. The relative comparison is the useful part.

## API details worth remembering

- MAI-Transcribe-1.5 is **not** a deployable model - there is no
  `az cognitiveservices account deployment create` step and it does not appear in the
  model catalog. It is selected per request.
- Batch: `POST /speechtotext/transcriptions:transcribe?api-version=2025-10-15` with
  `enhancedMode: {enabled: true, model: "mai-transcribe-1.5"}`. The `2024-11-15`
  api-version silently ignores `enhancedMode` and returns the standard model.
- Real-time: Voice Live WebSocket at
  `wss://{resource}.services.ai.azure.com/voice-live/realtime?api-version=2026-04-10&model=gpt-4.1`
  with `input_audio_transcription: {model: "mai-transcribe"}`.
- The classic Speech SDK **cannot** select MAI-Transcribe;
  `PropertyId.SpeechServiceConnection_RecoModelName` has no effect.