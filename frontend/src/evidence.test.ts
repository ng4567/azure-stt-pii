import { beforeAll, expect, test } from "bun:test";
import { Window } from "happy-dom";

import type { BenchmarkReport } from "./api.ts";
import { renderEvidence } from "./evidence.ts";
import type { CallSource } from "./source.ts";

beforeAll(() => {
  const window = new Window({ url: "http://localhost:3000" });
  globalThis.document = window.document as unknown as Document;
});

const report: BenchmarkReport = {
  audio_seconds: 120,
  channel_count: 1,
  channel_map: { "0": "speaker" },
  speaker_attributed: false,
  vad_utterances: 10,
  reference_words: null,
  scored: false,
  engines: {},
};

test("unscored uploaded evidence names the limitation and uses its own audio", () => {
  const host = document.createElement("div");
  const source: CallSource = {
    id: "job-1",
    label: "Your call — customer.wav",
    detail: "2m 0s · mono · no reference transcript",
    builtin: false,
    uploadId: "upload 1",
    report,
  };

  renderEvidence(host, source);

  expect(host.textContent).toContain("word error rate is not scored");
  expect(host.textContent).not.toContain("Scored against the reference above");
  expect(host.querySelector("audio")?.getAttribute("src")).toBe(
    "/api/uploads/upload%201/audio",
  );
  expect(host.querySelector(".repo-link")).toBeNull();
});
