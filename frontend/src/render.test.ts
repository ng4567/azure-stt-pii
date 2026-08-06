/** Smoke tests: the panels must render real API payloads without throwing. */
import { beforeAll, expect, test } from "bun:test";
import { Window } from "happy-dom";

import type { Job, UploadMeta } from "./api.ts";
import {
  describeUpload,
  renderCachedBenchmark,
  renderJobs,
  renderUploads,
} from "./render.ts";

beforeAll(() => {
  const window = new Window({ url: "http://localhost:3000" });
  // The render helpers use the ambient `document`, as they do in the browser.
  globalThis.document = window.document as unknown as Document;
});

const noopHandlers = { onDelete: () => {} };

const uploadWithBoth: UploadMeta = {
  id: "af80825c5e78",
  created_at: "2026-08-05T19:16:02.562078+00:00",
  audio: {
    filename: "clip-60s.wav",
    duration_seconds: 60,
    sample_rate: 24000,
    channels: 1,
    transcoded: false,
    size_bytes: 2880044,
  },
  transcript: { filename: "ref-60s.txt", characters: 1200, lines: 15 },
};

const transcriptOnly: UploadMeta = {
  id: "75c61647cee4",
  created_at: "2026-08-05T19:14:19.477872+00:00",
  audio: null,
  transcript: { filename: "mock-call-transcript.txt", characters: 6251, lines: 117 },
};

const succeededJob: Job = {
  id: "b3161e3896b9",
  upload_id: "8959d97694bb",
  status: "succeeded",
  created_at: "2026-08-05T19:16:03.000000+00:00",
  started_at: "2026-08-05T19:16:03.000000+00:00",
  finished_at: "2026-08-05T19:17:10.000000+00:00",
  scored: true,
  engines: {
    "architecture-1-azure-speech-realtime": "done",
    "architecture-2-mai-transcribe-realtime": "done",
    "architecture-3-mai-transcribe-batch": "done",
  },
  engine_labels: {
    "architecture-1-azure-speech-realtime": "1. Azure Speech real-time (SDK)",
    "architecture-2-mai-transcribe-realtime": "2. MAI-Transcribe-1.5 real-time",
    "architecture-3-mai-transcribe-batch": "3. MAI-Transcribe-1.5 batch",
  },
  architectures: {
    "architecture-1-azure-language": "done",
    "architecture-2-mai-realtime-deepseek": "done",
    "architecture-3-mai-batch-deepseek": "done",
  },
  architecture_labels: {
    "architecture-1-azure-language": "1. Azure Speech + Azure Language",
    "architecture-2-mai-realtime-deepseek": "2. MAI real-time + DeepSeek",
    "architecture-3-mai-batch-deepseek": "3. MAI batch + DeepSeek",
  },
  error: null,
  result: {
    audio_seconds: 60,
    channel_count: 1,
    channel_map: { "0": "speaker" },
    speaker_attributed: false,
    vad_utterances: 13,
    reference_words: 166,
    scored: true,
    engines: {
      "architecture-1-azure-speech-realtime": {
        label: "1. Azure Speech real-time (SDK)",
        transcript: "Thank you for calling Northstar Telecom.",
        conversation: {
          id: "clip-architecture-1",
          language: "en",
          modality: "transcript",
          speakerAttributed: true,
          channelMap: { "0": "REP", "1": "CUSTOMER" },
          conversationItems: [
            {
              id: "turn-0001",
              participantId: "REP",
              channel: 0,
              offset: 10_000_000,
              duration: 20_000_000,
              text: "Thank you for calling Northstar Telecom.",
            },
          ],
        },
        metrics: {
          mode: "real-time (incremental)",
          wall_seconds: 60.4,
          time_to_full_transcript: 60.4,
          finalization_lag: {
            mean: 0.7,
            median: 0.7,
            p95: 0.88,
            max: 0.9,
            count: 9,
          },
          segments: 9,
          word_count: 160,
          wer: 0.0843,
          accuracy: 0.9157,
          substitutions: 4,
          deletions: 8,
          insertions: 2,
          hits: 154,
          participants: {
            REP: {
              wer: 0.08,
              accuracy: 0.92,
              substitutions: 1,
              deletions: 1,
              insertions: 0,
              hits: 20,
            },
          },
        },
      },
      "architecture-3-mai-transcribe-batch": {
        label: "3. MAI-Transcribe-1.5 batch",
        transcript: "Thank you for calling North Star Telecom.",
        metrics: {
          mode: "batch (post-call)",
          wall_seconds: 2.1,
          turnaround_seconds: 2.1,
          time_to_full_transcript: 62.1,
          finalization_lag: {
            mean: null,
            median: null,
            p95: null,
            max: null,
            count: 0,
          },
          segments: 1,
          word_count: 168,
          wer: 0.0241,
          accuracy: 0.9759,
          participants: {
            REP: {
              wer: 0.04,
              accuracy: 0.96,
              substitutions: 1,
              deletions: 0,
              insertions: 0,
              hits: 24,
            },
          },
        },
      },
    },
    architectures: {
      "architecture-1-azure-language": {
        schema_version: "1.0",
        architecture_id: "architecture-1-azure-language",
        label: "1. Azure Speech + Azure Language",
        status: "succeeded",
        source: null,
        redacted: {
          transcript: "Call [PERSON].",
          conversation: {
            id: "clip-redacted",
            language: "en",
            modality: "transcript",
            speakerAttributed: true,
            channelMap: { "0": "REP" },
            conversationItems: [],
          },
        },
        summary: "[PERSON] requested help.",
        entities: [],
        stages: {
          stt: {
            status: "succeeded",
            provider: "Azure AI Speech",
            model: "real-time",
            wall_seconds: 60.4,
            metrics: { time_to_full_transcript: 60.4 },
            error: null,
          },
          pii_endpoint: {
            status: "succeeded",
            provider: "Azure AI Language",
            model: "Conversation PII",
            wall_seconds: 1.2,
            metrics: {},
            error: null,
          },
          summarizer_endpoint: {
            status: "succeeded",
            provider: "Azure AI Language",
            model: "Conversational Summarization",
            wall_seconds: 1.5,
            metrics: {},
            error: null,
          },
        },
        latency: {
          stt_seconds: 60.4,
          downstream_seconds: 1.6,
          end_to_end_seconds: 62,
        },
        error: null,
      },
      "architecture-2-mai-realtime-deepseek": {
        schema_version: "1.0",
        architecture_id: "architecture-2-mai-realtime-deepseek",
        label: "2. MAI real-time + DeepSeek",
        status: "succeeded",
        source: {
          transcript: "The customer requested account help.",
          conversation: {
            id: "clip-architecture-2",
            language: "en",
            modality: "transcript",
            speakerAttributed: true,
            channelMap: { "0": "REP", "1": "CUSTOMER" },
            conversationItems: [],
          },
        },
        redacted: null,
        summary: "The [PERSON] requested account help.",
        entities: [],
        stages: {
          stt: {
            status: "succeeded",
            provider: "Azure AI Foundry",
            model: "MAI-Transcribe-1.5 real-time",
            wall_seconds: 60.8,
            metrics: { time_to_full_transcript: 60.8 },
            error: null,
          },
          llm_api_call: {
            status: "succeeded",
            provider: "Azure AI Foundry",
            model: "DeepSeek V4 Flash",
            wall_seconds: 1.1,
            metrics: { input_tokens: 900, output_tokens: 80 },
            error: null,
          },
        },
        latency: {
          stt_seconds: 60.8,
          downstream_seconds: 1.1,
          end_to_end_seconds: 61.9,
        },
        error: null,
      },
      "architecture-3-mai-batch-deepseek": {
        schema_version: "1.0",
        architecture_id: "architecture-3-mai-batch-deepseek",
        label: "3. MAI batch + DeepSeek",
        status: "succeeded",
        source: {
          transcript: "The customer requested billing help.",
          conversation: {
            id: "clip-architecture-3",
            language: "en",
            modality: "transcript",
            speakerAttributed: true,
            channelMap: { "0": "REP", "1": "CUSTOMER" },
            conversationItems: [],
          },
        },
        redacted: null,
        summary: "The [PERSON] requested billing help.",
        entities: [],
        stages: {
          stt: {
            status: "succeeded",
            provider: "Azure AI Foundry",
            model: "MAI-Transcribe-1.5 batch",
            wall_seconds: 2.1,
            metrics: { time_to_full_transcript: 62.1 },
            error: null,
          },
          llm_api_call: {
            status: "succeeded",
            provider: "Azure AI Foundry",
            model: "DeepSeek V4 Flash",
            wall_seconds: 0.9,
            metrics: { input_tokens: 850, output_tokens: 70 },
            error: null,
          },
        },
        latency: {
          stt_seconds: 62.1,
          downstream_seconds: 0.9,
          end_to_end_seconds: 63,
        },
        error: null,
      },
    },
    pii_accuracy: {
      "architecture-1": {
        ground_truth_entities: 26,
        expected_entities: 25,
        predicted_entities: 25,
        unaligned_ground_truth_entities: 1,
        true_positives: 24,
        false_positives: 1,
        false_negatives: 1,
        precision: 0.96,
        recall: 0.96,
        f1: 0.96,
        category_accuracy: 1,
        pii_leakage_rate: 0.04,
        alignment_rate: 25 / 26,
      },
    },
  },
};

const failedEngineJob: Job = {
  ...succeededJob,
  id: "failedengine1",
  result: {
    audio_seconds: 60,
    channel_count: 1,
    channel_map: { "0": "speaker" },
    speaker_attributed: false,
    vad_utterances: 13,
    reference_words: null,
    scored: false,
    engines: {
      "architecture-2-mai-transcribe-realtime": {
        label: "2. MAI-Transcribe-1.5 real-time",
        error: "WebSocket closed unexpectedly",
        transcript: "",
      },
    },
  },
};

function panel(): HTMLElement {
  return document.createElement("div");
}

const builtinUpload: UploadMeta = {
  id: "mock-call",
  created_at: "2026-08-05T18:00:00.000000+00:00",
  builtin: true,
  label: "Mock call (checked-in fixture)",
  audio: {
    filename: "mock-call.wav",
    duration_seconds: 505.824,
    sample_rate: 24000,
    channels: 1,
    transcoded: false,
    size_bytes: 24279596,
  },
  transcript: { filename: "mock-call-transcript.txt", characters: 6251, lines: 117 },
};

test("uploads render as managed inputs, not separate benchmark actions", () => {
  const node = panel();
  renderUploads(node, [uploadWithBoth, transcriptOnly], noopHandlers);

  const buttons = node.querySelectorAll("button");
  expect(buttons.length).toBe(2);
  expect(node.textContent).toContain("af80825c5e78");
  expect(node.textContent).not.toContain("Transcribe + score");
  expect([...buttons].every((button) => button.textContent === "Delete")).toBe(true);
});

test("a built-in record has no per-upload action", () => {
  const node = panel();
  renderUploads(node, [builtinUpload], noopHandlers);

  const text = node.textContent ?? "";
  expect(text).toContain("Mock call (checked-in fixture)");
  expect(text).toContain("default");
  expect(text).toContain("mock-call.wav");
  expect(text).toContain("8m 26s");

  const buttons = [...node.querySelectorAll("button")];
  expect(buttons.length).toBe(0);
  expect(text).not.toContain("Delete");
});

test("empty state renders", () => {
  const node = panel();
  renderUploads(node, [], noopHandlers);
  expect(node.textContent).toContain("Nothing uploaded yet");

  const jobs = panel();
  renderJobs(jobs, []);
  expect(jobs.textContent).toContain("No benchmark runs yet");
});

test("a succeeded job renders the metrics table and transcripts", () => {
  const node = panel();
  renderJobs(node, [succeededJob]);

  const text = node.textContent ?? "";
  expect(text).toContain("8.43%");
  expect(text).toContain("97.59%");
  expect(text).toContain("62.1s"); // batch transcript-ready time
  expect(text).toContain("Scored against 166 reference words");
  expect(text).toContain("[1.00s] REP:");
  expect(text).toContain("Estimated processing cost");
  expect(text).toContain("Discounted total");
  expect(text).toContain("90% on Azure Speech and MAI-Transcribe");
  expect(text).toContain("PII redaction accuracy");
  expect(text).toContain("End-to-end architecture latency");
  expect(text).toContain("62.00s");
  expect(text).toContain("Conversation PII endpoint");
  expect(text).toContain("[PERSON] requested help.");
  expect(text).toContain("sanitized summary only");
  expect(text).toContain(
    "this architecture does not produce a redacted transcript or transcript entities",
  );
  expect(text).toContain("The [PERSON] requested account help.");
  expect(text).toContain("The [PERSON] requested billing help.");
  expect(text).not.toContain(
    "until the sanitized summary and redacted transcript are ready",
  );
  expect(text).toContain("96.00%");
  expect(text).toContain("24 / 1 / 1");
  expect(node.querySelector("h3")?.textContent).toBe("Estimated processing cost");
  const participantDetails = [...node.querySelectorAll("details")].find(
    (details) => details.querySelector("summary")?.textContent === "Per-participant WER",
  );
  expect(participantDetails).toBeDefined();
  expect(participantDetails?.open).toBe(false);
  expect(node.querySelectorAll(".winner").length).toBeGreaterThan(0);
  expect(text).toContain("Best");
  const sttTable = [...node.querySelectorAll("table")].find((table) =>
    table.textContent?.includes("WER"),
  );
  expect(sttTable?.querySelectorAll("tbody tr").length).toBe(2);
  expect(node.querySelectorAll("details").length).toBe(8);

  const architectureDetails = [...node.querySelectorAll(".architecture-detail")];
  const fullOutput = architectureDetails.find((details) =>
    details.querySelector("summary")?.textContent?.includes("Azure Speech + Azure Language")
  );
  const summaryOnly = architectureDetails.filter((details) =>
    details.querySelector("summary")?.textContent?.includes("sanitized summary only")
  );
  expect(fullOutput?.textContent).toContain("Redacted transcript");
  expect(summaryOnly).toHaveLength(2);
  expect(summaryOnly.every((details) => !details.textContent?.includes("Redacted transcript")))
    .toBe(true);
});

test("winner highlighting is recalculated for each new report", () => {
  const node = panel();
  const first = structuredClone(succeededJob.result!);
  renderCachedBenchmark(node, first);

  const sttTable = () => [...node.querySelectorAll("table")].find((table) =>
    table.textContent?.includes("Primary latency"),
  );
  const row = (label: string) => [...(sttTable()?.querySelectorAll("tbody tr") ?? [])]
    .find((candidate) => candidate.textContent?.includes(label));

  expect(row("MAI-Transcribe-1.5 batch")?.querySelectorAll(".winner").length).toBe(2);

  const updated = structuredClone(first);
  const azure = updated.engines["architecture-1-azure-speech-realtime"]!.metrics!;
  azure.wer = 0.01;
  azure.accuracy = 0.99;
  renderCachedBenchmark(node, updated);

  expect(row("Azure Speech real-time")?.querySelectorAll(".winner").length).toBeGreaterThan(2);
  expect(row("MAI-Transcribe-1.5 batch")?.querySelectorAll(".winner").length).toBe(0);
});

test("participant WER dropdown stays open across polling rerenders", () => {
  const node = panel();
  renderJobs(node, [succeededJob]);
  const participantDetails = [...node.querySelectorAll("details")].find(
    (details) => details.querySelector("summary")?.textContent === "Per-participant WER",
  );
  expect(participantDetails).toBeDefined();
  participantDetails!.open = true;

  renderJobs(node, [succeededJob]);

  const rerendered = [...node.querySelectorAll("details")].find(
    (details) => details.querySelector("summary")?.textContent === "Per-participant WER",
  );
  expect(rerendered?.open).toBe(true);
});

test("cached default results render as an architecture comparison", () => {
  const node = panel();
  const { pii_accuracy: _, architectures: __, ...historical } = succeededJob.result!;
  renderCachedBenchmark(node, historical);

  expect(node.textContent).toContain("End-to-end architecture latency");
  expect(node.textContent).toContain("predates the end-to-end pipeline timings");
  expect(node.firstElementChild?.textContent).toContain(
    "End-to-end architecture latency",
  );
  expect(node.textContent).toContain("Azure Speech real-time");
  expect(node.textContent).toContain("MAI-Transcribe-1.5 batch");
  expect(node.textContent).toContain("Scored against 166 reference words");
  expect(node.textContent).toContain("PII accuracy not scored");
});

test("a failed engine does not sink the rest of the report", () => {
  const node = panel();
  renderJobs(node, [failedEngineJob]);

  const text = node.textContent ?? "";
  expect(text).toContain("failed: WebSocket closed unexpectedly");
  expect(text).toContain("WER is not");
});

test("upload descriptions summarize audio and transcript", () => {
  expect(describeUpload(uploadWithBoth)).toContain("60s");
  expect(describeUpload(uploadWithBoth)).toContain("24.0 kHz mono");
  expect(describeUpload(transcriptOnly)).toContain("117 lines");
});
