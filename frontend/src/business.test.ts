/** The business case: section order, derived prose, and the stack tabs. */
import { beforeAll, expect, test } from "bun:test";
import { Window } from "happy-dom";

import type { ArchitectureResult, BenchmarkReport, EngineMetrics } from "./api.ts";
import { renderArchitectureTabs, renderBusinessCase } from "./business.ts";
import { ARCH_LEGACY, ARCH_MODERN, ENGINE_LEGACY, ENGINE_MODERN } from "./catalog.ts";
import { DEFAULT_SETTINGS } from "./pricing.ts";

beforeAll(() => {
  const window = new Window({ url: "http://localhost:3000" });
  globalThis.document = window.document as unknown as Document;
});

function metrics(wer: number | undefined): EngineMetrics {
  return {
    mode: "real-time",
    wall_seconds: 300,
    time_to_full_transcript: 300,
    finalization_lag: { mean: 0.7, median: 0.7, p95: 0.9, max: 1, count: 10 },
    segments: 10,
    word_count: 500,
    ...(wer === undefined ? {} : { wer, accuracy: 1 - wer }),
  };
}

function architecture(
  id: string,
  downstream: number,
  endToEnd: number,
): ArchitectureResult {
  return {
    schema_version: "1.0",
    architecture_id: id,
    label: id,
    status: "succeeded",
    source: null,
    redacted: null,
    summary: "A summary.",
    entities: [],
    stages: {},
    latency: { stt_seconds: 300, downstream_seconds: downstream, end_to_end_seconds: endToEnd },
    error: null,
  };
}

/** A five-minute stereo call with cached usage, so every projection is complete. */
function report(over: {
  scored?: boolean;
  legacyWer?: number;
  modernWer?: number;
} = {}): BenchmarkReport {
  const scored = over.scored ?? true;
  return {
    audio_seconds: 300,
    channel_count: 2,
    channel_map: { "0": "REP", "1": "CUSTOMER" },
    speaker_attributed: true,
    vad_utterances: 40,
    reference_words: scored ? 500 : null,
    scored,
    engines: {
      [ENGINE_LEGACY]: { label: "1. Azure Speech", metrics: metrics(scored ? over.legacyWer ?? 0.06 : undefined) },
      [ENGINE_MODERN]: { label: "2. MAI", metrics: metrics(scored ? over.modernWer ?? 0.03 : undefined) },
    },
    architectures: {
      [ARCH_LEGACY]: architecture(ARCH_LEGACY, 4.3, 304.3),
      [ARCH_MODERN]: architecture(ARCH_MODERN, 4.1, 304.1),
    },
    pricing_usage: {
      [ARCH_LEGACY]: {
        pii_input_characters: 5000,
        summary_input_characters: 5000,
        summary_output_characters: 800,
      },
      [ARCH_MODERN]: { deepseek_input_tokens: 2000, deepseek_output_tokens: 120 },
    },
  };
}

test("the scenario and why-now lead, then the saving, then what changes", () => {
  const host = document.createElement("div");
  renderBusinessCase(host, report(), DEFAULT_SETTINGS);

  const classes = [...host.children].map((child) => child.className);
  expect(classes[0]).toBe("context");
  expect(classes[1]).toBe("hero");
  expect(classes[2]).toContain("panel--deltas");

  const context = host.querySelector(".context")!;
  expect(context.firstElementChild?.className).toBe("scenario");
  expect(
    context.querySelector(".notice--retirement h2")?.textContent?.replace(/\u00a0/g, " "),
  ).toContain("March 31, 2029");
  expect(context.querySelector(".scenario h2")?.textContent).toContain("live agent handoff");
  // The stacks live on their own view; the deltas point there instead.
  expect(host.querySelector(".arch-card, .stack-tab")).toBeNull();
  expect(
    host.querySelector('.panel--deltas .panel__hint a[href="#architectures"]'),
  ).not.toBeNull();
});

test("the hero's accuracy claim is derived from the run rather than asserted", () => {
  const host = document.createElement("div");
  renderBusinessCase(host, report({ legacyWer: 0.06, modernWer: 0.03 }), DEFAULT_SETTINGS);
  const lede = host.querySelector(".hero__lede")!.textContent!.replace(/\s+/g, " ");
  expect(lede).toContain("cutting the transcription error rate from 6.00% to 3.00%");
  expect(lede).not.toContain("more than a third");

  renderBusinessCase(host, report({ scored: false }), DEFAULT_SETTINGS);
  const unscored = host.querySelector(".hero__lede")!.textContent!;
  expect(unscored).not.toContain("transcription error rate");

  renderBusinessCase(host, report({ legacyWer: 0.03, modernWer: 0.05 }), DEFAULT_SETTINGS);
  const worse = host.querySelector(".hero__lede")!.textContent!.replace(/\s+/g, " ");
  expect(worse).toContain("rises from 3.00% to 5.00%");
});

test("the deltas name the measured call length and colour by direction", () => {
  const host = document.createElement("div");
  renderBusinessCase(host, report({ legacyWer: 0.03, modernWer: 0.05 }), DEFAULT_SETTINGS);
  expect(host.querySelector(".panel--deltas .panel__hint")?.textContent).toContain("5m 0s");

  const tones = [...host.querySelectorAll(".delta")].map((item) => item.className);
  expect(tones[0]).toContain("delta--good"); // cost per call falls
  expect(tones[1]).toContain("delta--bad"); // WER rises on this run
  expect(tones[3]).toContain("delta--flat"); // end to end is about even
});

test("the stack tabs come from the catalog and point at the diagram pages", () => {
  const host = document.createElement("div");
  renderArchitectureTabs(host);

  const tabs = host.querySelectorAll<HTMLAnchorElement>("a.stack-tab");
  expect(tabs.length).toBe(2);
  expect(tabs[0]!.getAttribute("aria-selected")).toBe("true");
  expect(tabs[0]!.classList.contains("is-active")).toBe(true);
  expect(tabs[1]!.getAttribute("aria-selected")).toBe("false");
  expect(tabs[0]!.getAttribute("href")).toBe(`/api/architecture-diagrams/${ARCH_LEGACY}`);
  expect(tabs[1]!.getAttribute("href")).toBe(`/api/architecture-diagrams/${ARCH_MODERN}`);
  // The seller-facing names, in full — the old static tab dropped "Fabric".
  expect(tabs[1]!.querySelector(".stack-tab__name")?.textContent).toContain("Fabric");
  // Every tab is labelled by its name, not by its whole body.
  for (const tab of tabs) {
    const labelledBy = tab.getAttribute("aria-labelledby")!;
    expect(host.querySelector(`#${labelledBy}`)?.classList.contains("stack-tab__name")).toBe(true);
  }
});
