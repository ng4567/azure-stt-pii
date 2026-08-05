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
        },
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
  expect(node.querySelectorAll("tbody tr").length).toBe(2);
  expect(node.querySelectorAll("details").length).toBe(4);
});

test("cached default results render as an architecture comparison", () => {
  const node = panel();
  renderCachedBenchmark(node, succeededJob.result!);

  expect(node.textContent).toContain("Azure Speech real-time");
  expect(node.textContent).toContain("MAI-Transcribe-1.5 batch");
  expect(node.textContent).toContain("Scored against 166 reference words");
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
