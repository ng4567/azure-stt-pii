import { expect, test } from "bun:test";

import type { ArchitectureResult, BenchmarkReport } from "./api.ts";
import {
  DEFAULT_SETTINGS,
  blendedPiiRate,
  callUsage,
  conversationSummaryPlan,
  estimateArchitectureCosts,
  projectCosts,
  tieredPiiCost,
  type PricingSettings,
} from "./pricing.ts";

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
    latency: {
      stt_seconds: 1,
      downstream_seconds: 1,
      end_to_end_seconds: 2,
    },
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

/**
 * The checked-in stereo run, reduced to the usage pricing reads. These are the
 * numbers behind every dollar figure the app shows.
 */
const report: BenchmarkReport = {
  audio_seconds: 504.168,
  channel_count: 2,
  channel_map: { "0": "REP", "1": "CUSTOMER" },
  speaker_attributed: true,
  vad_utterances: 113,
  reference_words: 989,
  scored: true,
  engines: {},
  architectures: {
    "architecture-1-azure-language": architecture({
      pii_endpoint: { input_characters: 5_212 },
      summarizer_endpoint: { input_characters: 5_212, output_characters: 618 },
    }),
    "architecture-2-mai-realtime-deepseek": architecture({
      llm_api_call: { input_tokens: 2_361, output_tokens: 144 },
    }),
    // A retired architecture left behind in a saved report must not be priced.
    "architecture-3-mai-batch-deepseek": architecture({
      llm_api_call: { input_tokens: 2_416, output_tokens: 139 },
    }),
  },
};

const settings = (patch: Partial<PricingSettings> = {}): PricingSettings => ({
  ...DEFAULT_SETTINGS,
  ...patch,
});

test("prices only the two live architectures, ignoring retired keys in a saved report", () => {
  const costs = estimateArchitectureCosts(report, settings());
  expect(costs.map((cost) => cost.architectureId)).toEqual([
    "architecture-1-azure-language",
    "architecture-2-mai-realtime-deepseek",
  ]);
});

test("reproduces the published per-call list price of the measured run", () => {
  const [legacy, modern] = estimateArchitectureCosts(report, settings());

  // 0.280093 submitted audio hours x $1.00, 6 PII records x $0.001,
  // 6 summarization records x $0.002.
  expect(legacy!.listTotal).toBeCloseTo(0.298093, 6);
  // 0.280093 audio hours x $0.36, 2,361 input and 144 output tokens.
  expect(modern!.listTotal).toBeCloseTo(0.101356, 6);
  expect(legacy!.complete).toBe(true);
  expect(modern!.complete).toBe(true);
});

test("at list price the net cost equals the list cost", () => {
  for (const cost of estimateArchitectureCosts(report, settings())) {
    expect(cost.netTotal).toBeCloseTo(cost.listTotal, 10);
  }
});

test("each discount applies only to the services billed against it", () => {
  const [legacy, modern] = estimateArchitectureCosts(
    report,
    settings({ speechDiscount: 0.9, azureLanguageDiscount: 0.7 }),
  );

  // The documented contract: 90% off Speech, 70% off Language, none on the model.
  expect(legacy!.netTotal).toBeCloseTo(0.033409, 6);
  // MAI-Transcribe bills against the same Speech line, so it takes that discount too;
  // the model tokens are untouched at a 0% Foundry discount.
  expect(modern!.netTotal).toBeCloseTo(0.010605, 6);
});

test("the Foundry discount reaches the summarization tokens and nothing else", () => {
  const base = estimateArchitectureCosts(report, settings())[1]!;
  const discounted = estimateArchitectureCosts(
    report,
    settings({ foundryLlmDiscount: 0.5 }),
  )[1]!;

  const tokenList = base.components
    .filter((component) => component.family === "llm")
    .reduce((sum, component) => sum + (component.listCost ?? 0), 0);

  expect(base.netTotal - discounted.netTotal).toBeCloseTo(tokenList * 0.5, 10);
  const speech = discounted.components.find((component) => component.family === "speech")!;
  expect(speech.netCost).toBeCloseTo(speech.listCost!, 10);
});

test("missing usage yields a null component and an incomplete estimate", () => {
  const withoutTokens: BenchmarkReport = {
    ...report,
    architectures: {
      "architecture-1-azure-language": report.architectures![
        "architecture-1-azure-language"
      ]!,
      "architecture-2-mai-realtime-deepseek": architecture({}),
    },
  };
  const modern = estimateArchitectureCosts(withoutTokens, settings())[1]!;

  expect(modern.complete).toBe(false);
  expect(modern.components.some((component) => component.missing !== null)).toBe(true);
});

/* ----------------------------------------------------------- volume and tiers */

test("Conversation PII walks its volume ladder instead of billing tier one throughout", () => {
  // 500k records at $1.00, then 100k at $0.75.
  expect(tieredPiiCost(600_000)).toBeCloseTo(500 + 75, 6);
  expect(blendedPiiRate(600_000)).toBeCloseTo(575_000 / 600_000, 6);
  // Below the first threshold nothing blends.
  expect(blendedPiiRate(100_000)).toBeCloseTo(1, 6);
});

test("summarization uses pay-as-you-go until a commitment is actually cheaper", () => {
  expect(conversationSummaryPlan(600_000)).toEqual({
    label: "Standard pay-as-you-go",
    monthlyCost: 1_200,
  });
  expect(conversationSummaryPlan(6_000_000)).toEqual({
    label: "3M monthly commitment",
    monthlyCost: 6_600,
  });
  expect(conversationSummaryPlan(10_000_000)).toEqual({
    label: "10M monthly commitment",
    monthlyCost: 7_000,
  });
});

test("a high-volume projection includes the selected summarization commitment", () => {
  const projection = projectCosts(
    report,
    settings({ monthlyCalls: 1_000_000 }),
  );
  const annualLanguage = projection.legacy!.families.find(
    (entry) => entry.family === "language",
  )!.listCost;

  // 6M PII records walk the volume ladder to $3,050; summarization selects
  // the 3M commitment at $3,300 plus 3M overage at $1.10/1K = $6,600.
  expect(annualLanguage / 12).toBeCloseTo(3_050 + 6_600, 6);
});

test("projection scales usage by volume and average call length", () => {
  const single = projectCosts(report, settings({ monthlyCalls: 1 }));
  const many = projectCosts(report, settings({ monthlyCalls: 1_000 }));

  expect(many.modern!.monthlyNet).toBeCloseTo(single.modern!.perCallNet * 1_000, 6);
  expect(many.modern!.annualNet).toBeCloseTo(many.modern!.monthlyNet * 12, 6);

  // Doubling the call length doubles the audio hours, so the transcription line doubles.
  const longer = projectCosts(
    report,
    settings({ monthlyCalls: 1, averageCallMinutes: DEFAULT_SETTINGS.averageCallMinutes * 2 }),
  );
  const baseSpeech = single.modern!.families.find((entry) => entry.family === "speech")!;
  const longSpeech = longer.modern!.families.find((entry) => entry.family === "speech")!;
  expect(longSpeech.netCost).toBeCloseTo(baseSpeech.netCost * 2, 6);
});

test("at the measured call length the projection reproduces the per-call estimate", () => {
  // Guards the bug this caught: a hardcoded 8.4-minute default is 0.17s short of
  // the 504.168s benchmark call, which quietly rescaled every business-case figure
  // 0.03% below the per-call cost the technical view reports for the same run.
  const measuredMinutes = report.audio_seconds / 60;
  const projected = projectCosts(
    report,
    settings({ monthlyCalls: 1, averageCallMinutes: measuredMinutes }),
  );
  const [legacy, modern] = estimateArchitectureCosts(report, settings());

  expect(projected.lengthScale).toBeCloseTo(1, 12);
  expect(projected.legacy!.perCallList).toBeCloseTo(legacy!.listTotal, 10);
  expect(projected.modern!.perCallList).toBeCloseTo(modern!.listTotal, 10);
});

test("a shorter average call scales the projection down, not the measurement", () => {
  const measuredMinutes = report.audio_seconds / 60;
  const shorter = projectCosts(report, settings({ averageCallMinutes: measuredMinutes / 2 }));
  const full = projectCosts(report, settings({ averageCallMinutes: measuredMinutes }));

  const half = (family: string, p: typeof full) =>
    p.modern!.families.find((entry) => entry.family === family)!.netCost;
  expect(half("speech", shorter)).toBeCloseTo(half("speech", full) / 2, 8);
});

test("the modernized stack is the cheaper one, and the saving is reported as positive", () => {
  const projection = projectCosts(report, settings());
  expect(projection.modern!.annualNet).toBeLessThan(projection.legacy!.annualNet);
  expect(projection.annualSaving).toBeGreaterThan(0);
  expect(projection.savingPercent).toBeGreaterThan(0.5);
});

test("call usage reads submitted audio hours across both channels", () => {
  const usage = callUsage(report);
  expect(usage.audioHours).toBeCloseTo((504.168 * 2) / 3600, 6);
  expect(usage.piiCharacters).toBe(5_212);
  expect(usage.summaryCharacters).toBe(5_830);
  expect(usage.llmInputTokens).toBe(2_361);
});

test("a zero-volume projection stays finite rather than dividing by zero", () => {
  const projection = projectCosts(report, settings({ monthlyCalls: 0 }));
  expect(projection.annualSaving).toBe(0);
  expect(Number.isFinite(projection.blendedPiiRate)).toBe(true);
});
