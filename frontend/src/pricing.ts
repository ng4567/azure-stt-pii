/**
 * Cost model for the two architectures.
 *
 * Usage comes from the measured benchmark run; unit prices are Azure list price in
 * East US. The seller supplies three discounts and a volume, and everything is
 * recomputed from there — nothing about a discount is hardcoded in prose.
 */
import type { ArchitectureResult, BenchmarkReport } from "./api.ts";
import { ARCH_LEGACY, ARCH_MODERN, profile } from "./catalog.ts";

/** Which contract line a cost component is billed against. */
export type ServiceFamily = "speech" | "language" | "llm";

export const FAMILY_LABELS: Record<ServiceFamily, string> = {
  speech: "Azure AI Speech",
  language: "Azure AI Language",
  llm: "Foundry model",
};

export interface PricingRates {
  azureSpeechPerAudioHour: number;
  maiTranscribePerAudioHour: number;
  /** Conversation PII, first tier. Volume tiers apply above 0.5M records. */
  conversationPiiPerThousandRecords: number;
  conversationSummaryPerThousandRecords: number;
  deepSeekInputPerMillionTokens: number;
  deepSeekOutputPerMillionTokens: number;
}

export const pricingRates: PricingRates = {
  azureSpeechPerAudioHour: 1,
  maiTranscribePerAudioHour: 0.36,
  conversationPiiPerThousandRecords: 1,
  conversationSummaryPerThousandRecords: 2,
  deepSeekInputPerMillionTokens: 0.19,
  deepSeekOutputPerMillionTokens: 0.51,
};

export interface PricingDocumentationLink {
  label: string;
  description: string;
  url: string;
}

/** Official documentation behind every unit price used by the calculator. */
export const PRICING_DOCUMENTATION: PricingDocumentationLink[] = [
  {
    label: "Azure Speech pricing",
    description: "Speech-to-text and Fast Transcription audio-hour meters",
    url: "https://azure.microsoft.com/en-us/pricing/details/speech/",
  },
  {
    label: "MAI-Transcribe-1.5 model catalog",
    description: "Model-specific availability and pricing",
    url: "https://ai.azure.com/catalog/models/MAI-Transcribe-1.5",
  },
  {
    label: "Azure Language pricing",
    description: "Conversation PII, Standard summarization, and commitments",
    url: "https://azure.microsoft.com/en-us/pricing/details/language/",
  },
  {
    label: "DeepSeek pricing in Foundry",
    description: "Global Standard input and output token meters",
    url: "https://azure.microsoft.com/en-us/pricing/details/ai-foundry-models/deepseek/",
  },
  {
    label: "Azure Retail Prices API",
    description: "Microsoft documentation for verifying public regional meter prices",
    url: "https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices",
  },
];

/**
 * Conversation PII is tiered on monthly text-record volume. Ignoring the ladder
 * overstates the legacy cost at scale, which is exactly the error a customer
 * catches, so the projection walks the tiers.
 */
export const PII_TIERS: Array<{ upToRecords: number; perThousand: number }> = [
  { upToRecords: 500_000, perThousand: 1.0 },
  { upToRecords: 2_500_000, perThousand: 0.75 },
  { upToRecords: 10_000_000, perThousand: 0.3 },
  { upToRecords: Infinity, perThousand: 0.25 },
];

export function tieredPiiCost(records: number): number {
  let remaining = Math.max(0, records);
  let consumed = 0;
  let total = 0;
  for (const tier of PII_TIERS) {
    if (remaining <= 0) break;
    const capacity = tier.upToRecords - consumed;
    const inTier = Math.min(remaining, capacity);
    total += (inTier / 1_000) * tier.perThousand;
    remaining -= inTier;
    consumed += inTier;
  }
  return total;
}

/** The blended $/1,000 records at a given monthly volume. */
export function blendedPiiRate(records: number): number {
  if (records <= 0) return PII_TIERS[0]!.perThousand;
  return (tieredPiiCost(records) / records) * 1_000;
}

interface SummaryPlan {
  label: string;
  includedRecords: number;
  monthlyFee: number;
  overagePerThousand: number;
}

/**
 * Summarization commitments are monthly fixed fees, not per-call unit rates.
 * Standard remains a candidate so low-volume projections never pay for unused
 * commitment capacity.
 */
export const SUMMARY_PLANS: SummaryPlan[] = [
  {
    label: "Standard pay-as-you-go",
    includedRecords: 0,
    monthlyFee: 0,
    overagePerThousand: pricingRates.conversationSummaryPerThousandRecords,
  },
  {
    label: "3M monthly commitment",
    includedRecords: 3_000_000,
    monthlyFee: 3_300,
    overagePerThousand: 1.1,
  },
  {
    label: "10M monthly commitment",
    includedRecords: 10_000_000,
    monthlyFee: 7_000,
    overagePerThousand: 0.7,
  },
];

export interface SummaryPlanCost {
  label: string;
  monthlyCost: number;
}

export function conversationSummaryPlan(
  records: number,
  standardPerThousand: number = pricingRates.conversationSummaryPerThousandRecords,
): SummaryPlanCost {
  const volume = Math.max(0, records);
  const candidates = SUMMARY_PLANS.map((plan, index) => ({
    label: plan.label,
    monthlyCost:
      plan.monthlyFee +
      (Math.max(0, volume - plan.includedRecords) / 1_000) *
        (index === 0 ? standardPerThousand : plan.overagePerThousand),
  }));
  return candidates.reduce((best, candidate) =>
    candidate.monthlyCost < best.monthlyCost ? candidate : best,
  );
}

export interface PricingSettings {
  /** 0..1. Azure AI Speech: both the legacy recognizer and MAI-Transcribe. */
  speechDiscount: number;
  /** 0..1. Conversation PII and conversation summarization. */
  azureLanguageDiscount: number;
  /** 0..1. Foundry model tokens; stands in for provisioned (PTU) capacity. */
  foundryLlmDiscount: number;
  monthlyCalls: number;
  averageCallMinutes: number;
}

export const DEFAULT_SETTINGS: PricingSettings = {
  speechDiscount: 0,
  azureLanguageDiscount: 0,
  foundryLlmDiscount: 0,
  monthlyCalls: 100_000,
  averageCallMinutes: 8.4,
};

export function discountFor(
  family: ServiceFamily,
  settings: PricingSettings,
): number {
  if (family === "speech") return settings.speechDiscount;
  if (family === "language") return settings.azureLanguageDiscount;
  return settings.foundryLlmDiscount;
}

/* ------------------------------------------------------------ measured usage */

/**
 * What one call consumes, in billable units. Read from the measured run so the
 * model never invents usage it did not observe.
 */
export interface CallUsage {
  audioSeconds: number;
  channelCount: number;
  /** Submitted audio hours: both channels are transcribed independently. */
  audioHours: number;
  piiCharacters: number | null;
  summaryCharacters: number | null;
  llmInputTokens: number | null;
  llmOutputTokens: number | null;
}

function metric(
  result: ArchitectureResult | undefined,
  stages: string[],
  name: string,
): number | null {
  for (const stage of stages) {
    const value = result?.stages[stage]?.metrics[name];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function measured(
  report: BenchmarkReport,
  architectureId: string,
  result: ArchitectureResult | undefined,
  stages: string[],
  metricName: string,
  cachedName: string,
): number | null {
  const live = metric(result, stages, metricName);
  if (live !== null) return live;
  const cached = report.pricing_usage?.[architectureId]?.[cachedName];
  return typeof cached === "number" && Number.isFinite(cached) ? cached : null;
}

export function callUsage(report: BenchmarkReport): CallUsage {
  const architectures = report.architectures ?? {};
  const legacy = architectures[ARCH_LEGACY];
  const modern = architectures[ARCH_MODERN];
  const channelCount = Math.max(1, report.channel_count);
  const audioSeconds = report.audio_seconds;

  const summaryInput = measured(
    report, ARCH_LEGACY, legacy, ["summarizer_endpoint", "summarization"],
    "input_characters", "summary_input_characters",
  );
  const summaryOutput = measured(
    report, ARCH_LEGACY, legacy, ["summarizer_endpoint", "summarization"],
    "output_characters", "summary_output_characters",
  );

  return {
    audioSeconds,
    channelCount,
    audioHours: (audioSeconds * channelCount) / 3600,
    piiCharacters: measured(
      report, ARCH_LEGACY, legacy, ["pii_endpoint", "pii_redaction"],
      "input_characters", "pii_input_characters",
    ),
    summaryCharacters:
      summaryInput === null || summaryOutput === null
        ? null
        : summaryInput + summaryOutput,
    llmInputTokens: measured(
      report, ARCH_MODERN, modern, ["llm_api_call", "pii_redaction"],
      "input_tokens", "deepseek_input_tokens",
    ),
    llmOutputTokens: measured(
      report, ARCH_MODERN, modern, ["llm_api_call", "pii_redaction"],
      "output_tokens", "deepseek_output_tokens",
    ),
  };
}

/** Text records are billed per started 1,000 characters. */
export function textRecords(characters: number): number {
  return Math.ceil(characters / 1_000);
}

/* --------------------------------------------------------------- per-call cost */

export interface CostComponent {
  label: string;
  family: ServiceFamily;
  usage: string;
  listCost: number | null;
  netCost: number | null;
  /** Set when the run did not report the usage this component needs. */
  missing: string | null;
}

export interface ArchitectureCost {
  architectureId: string;
  label: string;
  components: CostComponent[];
  listTotal: number;
  netTotal: number;
  complete: boolean;
}

function component(
  label: string,
  family: ServiceFamily,
  usage: string,
  listCost: number | null,
  settings: PricingSettings,
  missing: string | null = null,
): CostComponent {
  const discount = discountFor(family, settings);
  return {
    label,
    family,
    usage,
    listCost,
    netCost: listCost === null ? null : listCost * (1 - discount),
    missing,
  };
}

function unavailable(
  label: string,
  family: ServiceFamily,
  settings: PricingSettings,
): CostComponent {
  return component(label, family, "usage unavailable", null, settings, `${label} usage`);
}

function totals(
  architectureId: string,
  components: CostComponent[],
): ArchitectureCost {
  return {
    architectureId,
    label: profile(architectureId)?.name ?? architectureId,
    components,
    listTotal: components.reduce((sum, item) => sum + (item.listCost ?? 0), 0),
    netTotal: components.reduce((sum, item) => sum + (item.netCost ?? 0), 0),
    complete: components.every((item) => item.listCost !== null),
  };
}

/** Scale measured usage to a different average call length. */
export function scaleUsage(usage: CallUsage, factor: number): CallUsage {
  const scale = (value: number | null): number | null =>
    value === null ? null : value * factor;
  return {
    ...usage,
    audioSeconds: usage.audioSeconds * factor,
    audioHours: usage.audioHours * factor,
    piiCharacters: scale(usage.piiCharacters),
    summaryCharacters: scale(usage.summaryCharacters),
    llmInputTokens: scale(usage.llmInputTokens),
    llmOutputTokens: scale(usage.llmOutputTokens),
  };
}

/**
 * Cost of one call from measured usage. `piiPerThousand` lets the projection price
 * Conversation PII at the blended tier rate for its monthly volume; per-call
 * display uses tier 1.
 */
export function costsFromUsage(
  usage: CallUsage,
  settings: PricingSettings = DEFAULT_SETTINGS,
  rates: PricingRates = pricingRates,
  piiPerThousand: number = rates.conversationPiiPerThousandRecords,
  summaryOverride?: { listCost: number; usage: string },
): ArchitectureCost[] {
  const hours = usage.audioHours.toFixed(6);

  const legacy: CostComponent[] = [
    component(
      "Azure Speech real-time STT",
      "speech",
      `${hours} submitted audio hours`,
      usage.audioHours * rates.azureSpeechPerAudioHour,
      settings,
    ),
    usage.piiCharacters === null
      ? unavailable("Conversation PII", "language", settings)
      : component(
          "Conversation PII",
          "language",
          `${usage.piiCharacters.toLocaleString()} characters = ${textRecords(usage.piiCharacters)} text records`,
          (textRecords(usage.piiCharacters) / 1_000) * piiPerThousand,
          settings,
        ),
    usage.summaryCharacters === null
      ? unavailable("Conversation summarization", "language", settings)
      : component(
          "Conversation summarization",
          "language",
          summaryOverride?.usage ??
            `${usage.summaryCharacters.toLocaleString()} characters = ${textRecords(usage.summaryCharacters)} text records`,
          summaryOverride?.listCost ??
            (textRecords(usage.summaryCharacters) / 1_000) *
              rates.conversationSummaryPerThousandRecords,
          settings,
        ),
  ];

  const modern: CostComponent[] = [
    component(
      "MAI-Transcribe-1.5 real-time",
      "speech",
      `${hours} submitted audio hours`,
      usage.audioHours * rates.maiTranscribePerAudioHour,
      settings,
    ),
    usage.llmInputTokens === null
      ? unavailable("Summarization — input tokens", "llm", settings)
      : component(
          "Summarization — input tokens",
          "llm",
          `${usage.llmInputTokens.toLocaleString()} tokens`,
          (usage.llmInputTokens / 1_000_000) * rates.deepSeekInputPerMillionTokens,
          settings,
        ),
    usage.llmOutputTokens === null
      ? unavailable("Summarization — output tokens", "llm", settings)
      : component(
          "Summarization — output tokens",
          "llm",
          `${usage.llmOutputTokens.toLocaleString()} tokens`,
          (usage.llmOutputTokens / 1_000_000) * rates.deepSeekOutputPerMillionTokens,
          settings,
        ),
  ];

  return [totals(ARCH_LEGACY, legacy), totals(ARCH_MODERN, modern)];
}

/** Per-call cost of the measured run itself, at tier-1 Conversation PII pricing. */
export function estimateArchitectureCosts(
  report: BenchmarkReport,
  settings: PricingSettings = DEFAULT_SETTINGS,
  rates: PricingRates = pricingRates,
): ArchitectureCost[] {
  return costsFromUsage(callUsage(report), settings, rates);
}

/* ------------------------------------------------------------------ projection */

export interface FamilyTotal {
  family: ServiceFamily;
  listCost: number;
  netCost: number;
}

export interface ArchitectureProjection {
  architectureId: string;
  label: string;
  kind: "legacy" | "modern";
  complete: boolean;
  perCallList: number;
  perCallNet: number;
  monthlyList: number;
  monthlyNet: number;
  annualList: number;
  annualNet: number;
  /** Annual net cost split by contract line, for the composition chart. */
  families: FamilyTotal[];
}

export interface Projection {
  settings: PricingSettings;
  /** Multiplier applied to measured usage for the seller's average call length. */
  lengthScale: number;
  /** Blended $/1,000 Conversation PII records at this monthly volume. */
  blendedPiiRate: number;
  architectures: ArchitectureProjection[];
  legacy: ArchitectureProjection | undefined;
  modern: ArchitectureProjection | undefined;
  annualSaving: number;
  savingPercent: number | null;
}

const FAMILY_ORDER: ServiceFamily[] = ["speech", "language", "llm"];

function familyTotals(components: CostComponent[]): FamilyTotal[] {
  return FAMILY_ORDER.map((family) => ({
    family,
    listCost: components
      .filter((item) => item.family === family)
      .reduce((sum, item) => sum + (item.listCost ?? 0), 0),
    netCost: components
      .filter((item) => item.family === family)
      .reduce((sum, item) => sum + (item.netCost ?? 0), 0),
  })).filter((entry) => entry.listCost > 0 || entry.netCost > 0);
}

/**
 * Scale the measured call to the seller's volume and average handle time.
 *
 * Scaling is linear in call length: audio hours, transcript characters, and prompt
 * tokens all grow with the length of the conversation. That is an approximation,
 * and the UI says so.
 */
export function projectCosts(
  report: BenchmarkReport,
  settings: PricingSettings = DEFAULT_SETTINGS,
  rates: PricingRates = pricingRates,
): Projection {
  const measuredSeconds = report.audio_seconds > 0 ? report.audio_seconds : 1;
  const lengthScale = (settings.averageCallMinutes * 60) / measuredSeconds;
  const calls = Math.max(0, settings.monthlyCalls);

  // Monthly Conversation PII records decide which tier the volume lands in.
  const usage = callUsage(report);
  const monthlyPiiRecords =
    usage.piiCharacters === null
      ? 0
      : textRecords(usage.piiCharacters * lengthScale) * calls;
  const piiRate = blendedPiiRate(monthlyPiiRecords);
  const monthlySummaryRecords =
    usage.summaryCharacters === null
      ? 0
      : textRecords(usage.summaryCharacters * lengthScale) * calls;
  const summaryPlan = conversationSummaryPlan(
    monthlySummaryRecords,
    rates.conversationSummaryPerThousandRecords,
  );

  const scaled = costsFromUsage(
    scaleUsage(usage, lengthScale),
    settings,
    rates,
    piiRate,
    usage.summaryCharacters === null
      ? undefined
      : {
          listCost: calls > 0 ? summaryPlan.monthlyCost / calls : 0,
          usage: `${monthlySummaryRecords.toLocaleString()} monthly text records · ${summaryPlan.label}`,
        },
  );

  const architectures = scaled.map((cost): ArchitectureProjection => {
    const kind = profile(cost.architectureId)?.kind ?? "modern";
    return {
      architectureId: cost.architectureId,
      label: cost.label,
      kind,
      complete: cost.complete,
      perCallList: cost.listTotal,
      perCallNet: cost.netTotal,
      monthlyList: cost.listTotal * calls,
      monthlyNet: cost.netTotal * calls,
      annualList: cost.listTotal * calls * 12,
      annualNet: cost.netTotal * calls * 12,
      families: familyTotals(cost.components).map((entry) => ({
        family: entry.family,
        listCost: entry.listCost * calls * 12,
        netCost: entry.netCost * calls * 12,
      })),
    };
  });

  const legacy = architectures.find((entry) => entry.kind === "legacy");
  const modern = architectures.find((entry) => entry.kind === "modern");
  const annualSaving =
    legacy && modern ? legacy.annualNet - modern.annualNet : 0;

  return {
    settings,
    lengthScale,
    blendedPiiRate: piiRate,
    architectures,
    legacy,
    modern,
    annualSaving,
    savingPercent:
      legacy && modern && legacy.annualNet > 0
        ? annualSaving / legacy.annualNet
        : null,
  };
}
