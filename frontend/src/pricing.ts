import type { ArchitectureResult, BenchmarkReport } from "./api.ts";

export interface PricingRates {
  azureSpeechPerAudioHour: number;
  maiTranscribePerAudioHour: number;
  conversationPiiPerThousandRecords: number;
  conversationSummaryPerTenMillionRecords: number;
  deepSeekInputPerMillionTokens: number;
  deepSeekCachedInputPerMillionTokens: number;
  deepSeekOutputPerMillionTokens: number;
  speechDiscount: number;
  azureLanguageDiscount: number;
}

export const pricingRates: PricingRates = {
  azureSpeechPerAudioHour: 1,
  maiTranscribePerAudioHour: 0.36,
  conversationPiiPerThousandRecords: 1,
  conversationSummaryPerTenMillionRecords: 7_000,
  deepSeekInputPerMillionTokens: 0.19,
  deepSeekCachedInputPerMillionTokens: 0.028,
  deepSeekOutputPerMillionTokens: 0.51,
  speechDiscount: 0.9,
  azureLanguageDiscount: 0.7,
};

export interface CostComponent {
  label: string;
  usage: string;
  listCost: number | null;
  discountedCost: number | null;
  missing: string | null;
}

export interface ArchitectureCost {
  architectureId: string;
  label: string;
  components: CostComponent[];
  listTotal: number;
  discountedTotal: number;
  complete: boolean;
}

function metric(result: ArchitectureResult | undefined, stage: string, name: string): number | null {
  const value = result?.stages[stage]?.metrics[name];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function metricFromStages(
  result: ArchitectureResult | undefined,
  stages: string[],
  name: string,
): number | null {
  for (const stage of stages) {
    const value = metric(result, stage, name);
    if (value !== null) return value;
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
  return metricFromStages(result, stages, metricName) ??
    report.pricing_usage?.[architectureId]?.[cachedName] ??
    null;
}

function component(
  label: string,
  usage: string,
  listCost: number | null,
  discount: number,
  missing: string | null = null,
): CostComponent {
  return {
    label,
    usage,
    listCost,
    discountedCost: listCost === null ? null : listCost * (1 - discount),
    missing,
  };
}

function audioComponent(
  label: string,
  audioSeconds: number,
  channelCount: number,
  rate: number,
  discount: number,
): CostComponent {
  const hours = (audioSeconds * channelCount) / 3600;
  return component(label, `${hours.toFixed(6)} submitted audio hours`, hours * rate, discount);
}

function textRecords(characters: number): number {
  return Math.ceil(characters / 1_000);
}

function languageComponent(
  label: string,
  characters: number | null,
  costPerRecord: number,
  discount: number,
): CostComponent {
  if (characters === null) {
    return component(label, "usage unavailable", null, discount, `${label} usage`);
  }
  const records = textRecords(characters);
  return component(
    label,
    `${characters.toLocaleString()} characters = ${records} text records`,
    records * costPerRecord,
    discount,
  );
}

function tokenComponent(
  label: string,
  tokens: number | null,
  rate: number,
): CostComponent {
  if (tokens === null) {
    return component(label, "usage unavailable", null, 0, `${label} usage`);
  }
  return component(
    label,
    `${tokens.toLocaleString()} tokens across all attempts`,
    (tokens / 1_000_000) * rate,
    0,
  );
}

function architectureCost(
  architectureId: string,
  label: string,
  components: CostComponent[],
): ArchitectureCost {
  return {
    architectureId,
    label,
    components,
    listTotal: components.reduce((total, item) => total + (item.listCost ?? 0), 0),
    discountedTotal: components.reduce((total, item) => total + (item.discountedCost ?? 0), 0),
    complete: components.every((item) => item.listCost !== null),
  };
}

export function estimateArchitectureCosts(
  report: BenchmarkReport,
  rates: PricingRates = pricingRates,
): ArchitectureCost[] {
  const architectures = report.architectures ?? {};
  const channels = Math.max(1, report.channel_count);
  const arch1Id = "architecture-1-azure-language";
  const arch2Id = "architecture-2-mai-realtime-deepseek";
  const arch3Id = "architecture-3-mai-batch-deepseek";
  const arch1 = architectures[arch1Id];
  const arch2 = architectures[arch2Id];
  const arch3 = architectures[arch3Id];
  const piiCharacters = measured(
    report, arch1Id, arch1, ["pii_endpoint", "pii_redaction"],
    "input_characters", "pii_input_characters",
  );
  const summaryInput = measured(
    report, arch1Id, arch1, ["summarizer_endpoint", "summarization"],
    "input_characters", "summary_input_characters",
  );
  const summaryOutput = measured(
    report, arch1Id, arch1, ["summarizer_endpoint", "summarization"],
    "output_characters", "summary_output_characters",
  );
  const summaryCharacters = summaryInput === null || summaryOutput === null
    ? null
    : summaryInput + summaryOutput;
  const piiCostPerRecord = rates.conversationPiiPerThousandRecords / 1_000;
  const summaryCostPerRecord = rates.conversationSummaryPerTenMillionRecords / 10_000_000;

  const deepSeekComponents = (
    architectureId: string,
    result: ArchitectureResult | undefined,
  ): CostComponent[] => [
    tokenComponent(
      "DeepSeek V4 Flash input",
      measured(
        report, architectureId, result, ["llm_api_call", "pii_redaction"],
        "input_tokens", "deepseek_input_tokens",
      ),
      rates.deepSeekInputPerMillionTokens,
    ),
    tokenComponent(
      "DeepSeek V4 Flash output",
      measured(
        report, architectureId, result, ["llm_api_call", "pii_redaction"],
        "output_tokens", "deepseek_output_tokens",
      ),
      rates.deepSeekOutputPerMillionTokens,
    ),
  ];

  return [
    architectureCost(arch1Id, "Azure Speech + Azure Language", [
      audioComponent(
        "Azure Speech STT", report.audio_seconds, channels,
        rates.azureSpeechPerAudioHour, rates.speechDiscount,
      ),
      languageComponent(
        "Conversation PII", piiCharacters, piiCostPerRecord, rates.azureLanguageDiscount,
      ),
      languageComponent(
        "Conversation summarization", summaryCharacters,
        summaryCostPerRecord, rates.azureLanguageDiscount,
      ),
    ]),
    architectureCost(arch2Id, "MAI real-time + DeepSeek", [
      audioComponent(
        "MAI-Transcribe real-time", report.audio_seconds, channels,
        rates.maiTranscribePerAudioHour, rates.speechDiscount,
      ),
      ...deepSeekComponents(arch2Id, arch2),
    ]),
    architectureCost(arch3Id, "MAI batch + DeepSeek", [
      audioComponent(
        "MAI-Transcribe batch", report.audio_seconds, channels,
        rates.maiTranscribePerAudioHour, rates.speechDiscount,
      ),
      ...deepSeekComponents(arch3Id, arch3),
    ]),
  ];
}
