import { beforeAll, expect, test } from "bun:test";
import { Window } from "happy-dom";

import type { BenchmarkReport, Job, UploadMeta } from "./api.ts";
import {
  BUILTIN_SOURCE_ID,
  collectSources,
  renderSourceBar,
  renderSourceNotice,
} from "./source.ts";

/** happy-dom's Event class, which its own dispatchEvent insists on. */
let DomEvent: typeof Event;

beforeAll(() => {
  const window = new Window({ url: "http://localhost:3000" });
  globalThis.document = window.document as unknown as Document;
  DomEvent = window.Event as unknown as typeof Event;
});

const report = (seconds: number, scored = true): BenchmarkReport => ({
  audio_seconds: seconds,
  channel_count: 2,
  channel_map: { "0": "REP", "1": "CUSTOMER" },
  speaker_attributed: true,
  vad_utterances: 10,
  reference_words: scored ? 100 : null,
  scored,
  engines: {},
});

const job = (over: Partial<Job>): Job => ({
  id: "job1",
  upload_id: "upload1",
  status: "succeeded",
  created_at: "2026-08-18T10:00:00Z",
  started_at: "2026-08-18T10:00:00Z",
  finished_at: "2026-08-18T10:09:00Z",
  scored: true,
  engines: {},
  engine_labels: {},
  architectures: {},
  architecture_labels: {},
  result: report(300),
  error: null,
  ...over,
});

const upload = (over: Partial<UploadMeta>): UploadMeta => ({
  id: "upload1",
  created_at: "2026-08-18T09:59:00Z",
  audio: {
    filename: "customer-call.wav",
    duration_seconds: 300,
    sample_rate: 16000,
    channels: 2,
    transcoded: false,
    size_bytes: 1000,
  },
  transcript: null,
  ...over,
});

test("the built-in call is offered first, and alone until a run completes", () => {
  const sources = collectSources(report(504), [], []);
  expect(sources).toHaveLength(1);
  expect(sources[0]!.id).toBe(BUILTIN_SOURCE_ID);
  expect(sources[0]!.builtin).toBe(true);
});

test("the built-in source does not render a sample-call notice", () => {
  const host = document.createElement("div");
  const source = collectSources(report(504), [], [])[0];
  renderSourceNotice(host, source);
  expect(host.childElementCount).toBe(0);
});

test("a completed run of an uploaded call becomes a selectable source", () => {
  const sources = collectSources(report(504), [job({})], [upload({})]);
  expect(sources.map((source) => source.id)).toEqual([BUILTIN_SOURCE_ID, "job1"]);
  expect(sources[1]!.label).toContain("customer-call.wav");
  expect(sources[1]!.builtin).toBe(false);
  expect(sources[1]!.uploadId).toBe("upload1");
  expect(sources[1]!.detail).toContain("5m 0s");
});

test("runs that have not produced a report are not offered", () => {
  const sources = collectSources(
    report(504),
    [
      job({ id: "running", status: "running", result: null }),
      job({ id: "failed", status: "failed", result: null }),
    ],
    [upload({})],
  );
  expect(sources.map((source) => source.id)).toEqual([BUILTIN_SOURCE_ID]);
});

test("completed jobs whose upload was deleted are not offered", () => {
  const sources = collectSources(report(504), [job({})], []);
  expect(sources.map((source) => source.id)).toEqual([BUILTIN_SOURCE_ID]);
});

test("re-running the built-in upload is still labelled as the built-in call", () => {
  const sources = collectSources(
    report(504),
    [job({ id: "rerun", upload_id: "mock-call-stereo" })],
    [upload({ id: "mock-call-stereo", builtin: true, label: "Mock call" })],
  );
  expect(sources[1]!.builtin).toBe(true);
  expect(sources[1]!.label).toContain("Built-in sample call");
  expect(sources[1]!.label).not.toContain("Your call");
});

test("an unscored run says so rather than implying word error rate was measured", () => {
  const sources = collectSources(
    null,
    [job({ result: report(120, false) })],
    [upload({})],
  );
  expect(sources[0]!.detail).toContain("no reference transcript");
});

test("a lone recording is named in prose and offers no picker", () => {
  const host = document.createElement("div");
  renderSourceBar(host, collectSources(report(504), [], []), BUILTIN_SOURCE_ID, {
    onSelect: () => {},
    onUploadRequest: () => {},
  });
  expect(host.querySelector("select")).toBeNull();
  expect(host.querySelector(".sourcebar__name")?.textContent).toBe("Built-in sample call");
  expect(host.querySelector(".sourcebar__detail")?.textContent).toContain("8m 24s");
  expect(host.querySelector("#source-upload")?.textContent?.trim()).toBe(
    "Attach an approved test call",
  );
});

test("selecting a different recording reports the choice", () => {
  const host = document.createElement("div");
  const chosen: string[] = [];
  const sources = collectSources(report(504), [job({})], [upload({})]);
  renderSourceBar(host, sources, BUILTIN_SOURCE_ID, {
    onSelect: (id) => chosen.push(id),
    onUploadRequest: () => {},
  });

  const select = host.querySelector("select")!;
  expect(select.options.length).toBe(2);
  select.value = "job1";
  select.dispatchEvent(new DomEvent("change", { bubbles: true }));
  expect(chosen).toEqual(["job1"]);
  expect(host.querySelector("#source-upload")?.textContent?.trim()).toBe(
    "Run another test call",
  );
});
