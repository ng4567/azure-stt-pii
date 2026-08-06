import { expect, test } from "bun:test";

import type { ArchitectureResult, BenchmarkReport } from "./api.ts";
import { estimateArchitectureCosts, type PricingRates } from "./pricing.ts";

function architecture(metrics: Record<string, Record<string, number>>): ArchitectureResult {
  return {
    schema_version: "1.0",
    architecture_id: "test",
    label: "test",
    status: "succeeded",
    source: null,
    redacted: null,
    summary: "summary",
    entities: [],
    stages: Object.fromEntries(
      Object.entries(metrics).map(([stage, values]) => [stage, {
        status: "succeeded",
        provider: "test",
        model: "test",
        wall_seconds: 1,
        metrics: values,
        error: null,
      }]),
    ),
    error: null,
  };
}

const report: BenchmarkReport = {
  audio_seconds: 3600,
  channel_count: 2,
  channel_map: { "0": "REP", "1": "CUSTOMER" },
  speaker_attributed: true,
  vad_utterances: 10,
  reference_words: null,
  scored: false,
  engines: {},
  architectures: {
    "architecture-1-azure-language": architecture({
      pii_redaction: { input_characters: 2_000 },
      summarization: { input_characters: 2_000, output_characters: 500 },
    }),
    "architecture-2-mai-realtime-deepseek": architecture({
      pii_redaction: { input_tokens: 1_000_000, output_tokens: 250_000 },
    }),
    "architecture-3-mai-batch-deepseek": architecture({
      pii_redaction: { input_tokens: 500_000, output_tokens: 100_000 },
    }),
  },
};

const rates: PricingRates = {
  azureSpeechPerAudioHour: 1,
  maiTranscribePerAudioHour: 0.36,
  conversationPiiPerThousandRecords: 0.01,
  conversationSummaryPerTenMillionRecords: 200_000,
  deepSeekInputPerMillionTokens: 0.5,
  deepSeekCachedInputPerMillionTokens: 0.05,
  deepSeekOutputPerMillionTokens: 1.5,
  speechDiscount: 0.9,
  azureLanguageDiscount: 0.7,
};

test("estimates each architecture from submitted audio and measured usage", () => {
  const estimates = estimateArchitectureCosts(report, rates);
  expect(estimates).toHaveLength(3);
  const azure = estimates[0]!;
  const realtime = estimates[1]!;
  const batch = estimates[2]!;

  expect(azure.listTotal).toBeCloseTo(2 + 0.00002 + 0.06);
  expect(azure.discountedTotal).toBeCloseTo(0.2 + 0.000006 + 0.018);
  expect(azure.complete).toBe(true);
  expect(realtime.listTotal).toBeCloseTo(0.72 + 0.5 + 0.375);
  expect(realtime.discountedTotal).toBeCloseTo(0.072 + 0.5 + 0.375);
  expect(realtime.complete).toBe(true);
  expect(batch.listTotal).toBeCloseTo(0.72 + 0.25 + 0.15);
  expect(batch.complete).toBe(true);
});

test("prices the cached benchmark usage with confirmed rates and discounts", () => {
  const benchmarkReport: BenchmarkReport = {
    ...report,
    audio_seconds: 504.168,
    architectures: {},
    pricing_usage: {
      "architecture-1-azure-language": {
        pii_input_characters: 5_212,
        summary_input_characters: 5_212,
        summary_output_characters: 660,
      },
      "architecture-2-mai-realtime-deepseek": {
        deepseek_input_tokens: 6_906,
        deepseek_output_tokens: 6_743,
      },
      "architecture-3-mai-batch-deepseek": {
        deepseek_input_tokens: 7_124,
        deepseek_output_tokens: 6_816,
      },
    },
  };

  const estimates = estimateArchitectureCosts(benchmarkReport);
  expect(estimates[0]!.listTotal).toBeCloseTo(0.290293, 6);
  expect(estimates[0]!.discountedTotal).toBeCloseTo(0.031069, 6);
  expect(estimates[1]!.listTotal).toBeCloseTo(0.105585, 6);
  expect(estimates[1]!.discountedTotal).toBeCloseTo(0.014834, 6);
  expect(estimates[2]!.listTotal).toBeCloseTo(0.105663, 6);
  expect(estimates[2]!.discountedTotal).toBeCloseTo(0.014913, 6);
});

test("does not present missing usage as a zero-cost component", () => {
  const estimates = estimateArchitectureCosts({ ...report, architectures: undefined });
  const azure = estimates[0]!;
  const pii = azure.components.find((component) => component.label === "Conversation PII");

  expect(pii?.listCost).toBeNull();
  expect(pii?.missing).toContain("usage");
  expect(azure.complete).toBe(false);
});
