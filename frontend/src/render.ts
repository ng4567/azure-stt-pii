/** Technical detail: the measured tables, uploads, and run history. State in, DOM out. */
import type {
  ArchitectureResult,
  ArchitectureStage,
  BenchmarkReport,
  EngineResult,
  Job,
  UploadMeta,
} from "./api.ts";
import {
  ARCHITECTURE_ORDER,
  ENGINE_ORDER,
  PROFILES,
  isKnownArchitecture,
  isKnownEngine,
  orderedEntries,
} from "./catalog.ts";
import {
  escapeHtml,
  formatBytes,
  formatDuration,
  formatPercent,
  formatSeconds,
  formatUnitCost,
} from "./format.ts";
import {
  DEFAULT_SETTINGS,
  FAMILY_LABELS,
  discountFor,
  estimateArchitectureCosts,
  type PricingSettings,
} from "./pricing.ts";
import { renderPricingSources } from "./pricing-sources.ts";

export {
  formatBytes,
  formatDuration,
  formatPercent,
  formatSeconds,
} from "./format.ts";

type WinnerDirection = "min" | "max";

/** Null unless at least two entries can be compared — a lone row has no winner. */
function bestValue(
  values: Array<number | null | undefined>,
  direction: WinnerDirection,
): number | null {
  const finite = values.filter(
    (value): value is number => typeof value === "number" && Number.isFinite(value),
  );
  if (finite.length < 2) return null;
  return direction === "min" ? Math.min(...finite) : Math.max(...finite);
}

function winnerCell(
  content: string,
  value: number | null | undefined,
  best: number | null,
  className = "numeric",
): string {
  const winner = best !== null && value === best;
  return `<td class="${className}${winner ? " winner" : ""}">${content}${
    winner ? ` <span class="winner-badge">Best</span>` : ""
  }</td>`;
}

/** The single best entry by `pick`, used for the at-a-glance scorecard. */
function leader<T>(
  items: T[],
  pick: (item: T) => number | null | undefined,
  direction: WinnerDirection,
): { item: T; value: number } | null {
  let best: { item: T; value: number } | null = null;
  for (const item of items) {
    const value = pick(item);
    if (typeof value !== "number" || !Number.isFinite(value)) continue;
    if (best === null || (direction === "min" ? value < best.value : value > best.value)) {
      best = { item, value };
    }
  }
  return best;
}

const STAGE_LABELS: Record<string, string> = {
  stt: "STT transcript ready",
  pii_endpoint: "Conversation PII endpoint",
  summarizer_endpoint: "Summarizer endpoint",
  regex_detection: "Regex candidate scan",
  request_preparation: "Backend request preparation + Entra auth",
  llm_api_call: "LLM API call (sanitized summary)",
  response_validation: "Backend response validation",
  transcript_redaction: "Transcript entity redaction",
  summary_sanitization: "Summary entity redaction",
  backend_overhead: "Backend orchestration overhead",
  pii_redaction: "PII redaction",
  summarization: "Summarization",
};

const SUMMARY_ONLY_NOTE = "sanitized summary only";

function stageDuration(stageKey: string, stage: ArchitectureStage): number {
  if (stageKey === "stt") {
    const ready = stage.metrics.time_to_full_transcript;
    if (typeof ready === "number" && Number.isFinite(ready)) return ready;
  }
  return stage.wall_seconds;
}

function metricNumber(stage: ArchitectureStage, key: string): number | null {
  const value = stage.metrics[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stageMetricSummary(stageKey: string, stage: ArchitectureStage): string {
  if (stageKey === "request_preparation") {
    const turns = metricNumber(stage, "source_turn_count");
    const segments = metricNumber(stage, "projected_segment_count");
    const characters = metricNumber(stage, "user_content_characters");
    if (turns !== null && segments !== null && characters !== null) {
      return `${turns.toLocaleString()} turns → ${segments.toLocaleString()} compact segments · ${characters.toLocaleString()} prompt characters`;
    }
  }
  if (stageKey === "llm_api_call") {
    const input = metricNumber(stage, "input_tokens");
    const output = metricNumber(stage, "output_tokens");
    if (input !== null && output !== null) {
      return `${input.toLocaleString()} input · ${output.toLocaleString()} output tokens`;
    }
  }
  return "";
}

function isSummaryOnly(result: ArchitectureResult): boolean {
  return result.status === "succeeded" && result.redacted === null;
}

/** Labels are numbered upstream ("1. Azure Speech …"); the number becomes a marker. */
function labelParts(label: string): { index: string | null; name: string } {
  const match = /^(\d+)\.\s*(.+)$/.exec(label);
  return match?.[1] && match[2]
    ? { index: match[1], name: match[2] }
    : { index: null, name: label };
}

function shortLabel(label: string): string {
  return labelParts(label).name;
}

function labelCell(label: string, suffix = "", architectureId = ""): string {
  const { index, name } = labelParts(label);
  const fallback = /^architecture-(\d+)/.exec(architectureId)?.[1] ?? null;
  const number = index ?? fallback;
  const marker = number ? `<span class="arch-cell__index">${escapeHtml(number)}</span>` : "";
  return `<td><span class="arch-cell"><span class="arch-cell__name">${marker}<span>${escapeHtml(name)}</span></span>${suffix}</span></td>`;
}

function summaryOnlyChip(result: ArchitectureResult): string {
  return isSummaryOnly(result) ? `<span class="chip">${SUMMARY_ONLY_NOTE}</span>` : "";
}

/** Resolves display labels for report keys that omit the pipeline suffix. */
function architectureLabel(report: BenchmarkReport, architectureId: string): string {
  const architectures = report.architectures ?? {};
  const exact = architectures[architectureId];
  if (exact) return exact.label;
  const prefixed = Object.entries(architectures).find(([key]) =>
    key.startsWith(`${architectureId}-`),
  );
  return prefixed ? prefixed[1].label : architectureId;
}

function table(innerHTML: string): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const element = document.createElement("table");
  element.innerHTML = innerHTML;
  wrap.append(element);
  return wrap;
}

function note(text: string): HTMLElement {
  const paragraph = document.createElement("p");
  paragraph.className = "section-note";
  paragraph.textContent = text;
  return paragraph;
}

function section(title: string, className = "comparison-section"): HTMLElement {
  const element = document.createElement("section");
  element.className = className;
  const heading = document.createElement("h3");
  heading.textContent = title;
  element.append(heading);
  return element;
}

function architectureEntries(report: BenchmarkReport): ArchitectureResult[] {
  return orderedEntries(report.architectures, ARCHITECTURE_ORDER).map(([, result]) => result);
}

function engineEntries(report: BenchmarkReport): Array<[string, EngineResult]> {
  return orderedEntries(report.engines, ENGINE_ORDER);
}

/** PII scoring is keyed by architecture id, and retired ids must not surface. */
function piiEntries(report: BenchmarkReport): Array<[string, NonNullable<BenchmarkReport["pii_accuracy"]>[string]]> {
  return Object.entries(report.pii_accuracy ?? {}).filter(([key]) =>
    isKnownArchitecture(key),
  );
}

interface Kpi {
  label: string;
  value: string;
  meta: string;
}

/** At-a-glance winners, so a report reads without scanning every table. */
export function renderScorecard(
  report: BenchmarkReport,
  settings: PricingSettings = DEFAULT_SETTINGS,
): HTMLElement {
  const architectures = architectureEntries(report).filter(
    (result) => result.status === "succeeded",
  );
  const fastest = leader(
    architectures,
    (result) => result.latency?.end_to_end_seconds,
    "min",
  );
  const cheapest = leader(
    estimateArchitectureCosts(report, settings).filter((estimate) => estimate.complete),
    (estimate) => estimate.netTotal,
    "min",
  );
  const mostAccurate = leader(
    engineEntries(report).map(([, entry]) => entry),
    (entry) => entry.metrics?.wer,
    "min",
  );
  const bestPii = leader(piiEntries(report), ([, metrics]) => metrics.f1, "max");

  const kpis: Kpi[] = [
    {
      label: "Call length",
      value: formatDuration(report.audio_seconds),
      meta: `${report.channel_count === 2 ? "stereo" : "mono"} · ${
        report.vad_utterances
      } utterances`,
    },
    {
      label: "Fastest end to end",
      value: fastest ? formatSeconds(fastest.value) : "—",
      meta: fastest ? shortLabel(fastest.item.label) : "no pipeline timings",
    },
    {
      label: "Lowest cost per call",
      value: cheapest ? formatUnitCost(cheapest.value) : "—",
      meta: cheapest ? shortLabel(cheapest.item.label) : "usage missing",
    },
    {
      label: "Best WER",
      value: mostAccurate ? formatPercent(mostAccurate.value) : "—",
      meta: mostAccurate ? shortLabel(mostAccurate.item.label) : "no reference transcript",
    },
    {
      label: "Best PII F1",
      value: bestPii ? formatPercent(bestPii.value) : "—",
      meta: bestPii
        ? shortLabel(architectureLabel(report, bestPii.item[0]))
        : "no ground truth",
    },
  ];

  const list = document.createElement("ul");
  list.className = "scorecard";
  list.append(
    ...kpis.map((kpi) => {
      const item = document.createElement("li");
      item.className = `kpi${kpi.value === "—" ? " kpi--empty" : ""}`;
      const label = document.createElement("span");
      label.className = "kpi__label";
      label.textContent = kpi.label;
      const value = document.createElement("span");
      value.className = "kpi__value";
      value.textContent = kpi.value;
      const meta = document.createElement("span");
      meta.className = "kpi__meta";
      meta.textContent = kpi.meta;
      meta.title = kpi.meta;
      item.append(label, value, meta);
      return item;
    }),
  );
  return list;
}

function renderArchitectureResults(report: BenchmarkReport): HTMLElement {
  const entries = architectureEntries(report);
  const container = section("End-to-end architecture latency", "architecture-results");

  if (entries.length === 0) {
    container.append(
      note(
        "This saved comparison predates the end-to-end pipeline timings. Start a benchmark to measure STT, downstream stages, and total latency for both architectures.",
      ),
    );
    return container;
  }

  const successful = entries.filter(
    (result) => result.status === "succeeded" && result.latency,
  );
  const bestEndToEnd = bestValue(
    successful.map((result) => result.latency?.end_to_end_seconds),
    "min",
  );
  const bestStt = bestValue(
    successful.map((result) => result.latency?.stt_seconds),
    "min",
  );
  const bestDownstream = bestValue(
    successful.map((result) => result.latency?.downstream_seconds),
    "min",
  );
  container.append(
    table(`
    <thead><tr><th>Architecture</th><th>End to end</th><th>STT ready</th><th>Downstream</th></tr></thead>
    <tbody>${entries.map((result: ArchitectureResult) => {
      if (result.status === "failed" || !result.latency) {
        return `<tr>${labelCell(result.label)}<td colspan="3">failed: ${escapeHtml(
          result.error ?? "unknown error",
        )}</td></tr>`;
      }
      return `<tr>
        ${labelCell(result.label, summaryOnlyChip(result))}
        ${winnerCell(
          `<strong>${formatSeconds(result.latency.end_to_end_seconds)}</strong>`,
          result.latency.end_to_end_seconds,
          bestEndToEnd,
        )}
        ${winnerCell(
          formatSeconds(result.latency.stt_seconds),
          result.latency.stt_seconds,
          bestStt,
        )}
        ${winnerCell(
          formatSeconds(result.latency.downstream_seconds),
          result.latency.downstream_seconds,
          bestDownstream,
        )}
      </tr>`;
    }).join("")}</tbody>`),
    note(
      "End to end runs from the start of the call until each architecture's declared final outputs are ready. Parallel endpoint times overlap and are not added together.",
    ),
  );

  for (const result of entries) {
    const details = document.createElement("details");
    details.className = "architecture-detail";
    const summary = document.createElement("summary");
    summary.textContent = `Pipeline stages and outputs — ${result.label}${
      isSummaryOnly(result) ? ` — ${SUMMARY_ONLY_NOTE}` : ""
    }`;
    details.append(summary);

    if (result.status === "failed") {
      const error = document.createElement("p");
      error.className = "message error";
      error.textContent = result.error ?? "Architecture failed.";
      details.append(error);
      container.append(details);
      continue;
    }

    if (isSummaryOnly(result)) {
      details.append(
        note(
          "Sanitized summary only; this architecture does not produce a redacted transcript or transcript entities.",
        ),
      );
    }

    details.append(
      table(`
      <thead><tr><th>Stage</th><th>Provider / model</th><th>Latency</th></tr></thead>
      <tbody>${Object.entries(result.stages).map(([key, stage]) => {
        const metrics = stageMetricSummary(key, stage);
        return `
        <tr>
          <td>${escapeHtml(STAGE_LABELS[key] ?? key.replaceAll("_", " "))}</td>
          <td>${escapeHtml(stage.provider)} · ${escapeHtml(stage.model)}${
            metrics ? `<span class="stage-metrics">${escapeHtml(metrics)}</span>` : ""
          }</td>
          <td class="numeric">${formatSeconds(stageDuration(key, stage))}</td>
        </tr>`;
      }).join("")}</tbody>`),
    );

    if (result.summary) {
      const summaryHeading = document.createElement("h4");
      summaryHeading.textContent = "Sanitized summary";
      const summaryText = document.createElement("pre");
      summaryText.textContent = result.summary;
      details.append(summaryHeading, summaryText);
    }
    if (result.redacted?.transcript) {
      const transcriptHeading = document.createElement("h4");
      transcriptHeading.textContent = "Redacted transcript";
      const transcriptText = document.createElement("pre");
      transcriptText.textContent = result.redacted.transcript;
      details.append(transcriptHeading, transcriptText);
    }
    container.append(details);
  }

  return container;
}

function renderPricing(
  report: BenchmarkReport,
  settings: PricingSettings,
): HTMLElement {
  const container = section(
    "Per-call cost breakdown",
    "comparison-section pricing-results",
  );

  const costs = estimateArchitectureCosts(report, settings);
  const complete = costs.filter((estimate) => estimate.complete);
  const bestList = bestValue(complete.map((estimate) => estimate.listTotal), "min");
  const bestNet = bestValue(complete.map((estimate) => estimate.netTotal), "min");
  container.append(
    table(`
    <thead><tr><th>Architecture</th><th>Components</th><th>List total</th><th>Your rate</th></tr></thead>
    <tbody>${costs.map((estimate) => `<tr>
        ${labelCell(estimate.label, "", estimate.architectureId)}
        <td class="cost-components">${estimate.components
          .map(
            (component) =>
              `${escapeHtml(component.label)}: ${escapeHtml(component.usage)} (${
                component.listCost === null ? "—" : formatUnitCost(component.listCost)
              } list → ${
                component.netCost === null ? "—" : formatUnitCost(component.netCost)
              })`,
          )
          .join("<br>")}</td>
        ${winnerCell(
          formatUnitCost(estimate.listTotal),
          estimate.complete ? estimate.listTotal : null,
          bestList,
        )}
        ${winnerCell(
          formatUnitCost(estimate.netTotal),
          estimate.complete ? estimate.netTotal : null,
          bestNet,
        )}
      </tr>`).join("")}</tbody>`),
  );

  const applied = (["speech", "language", "llm"] as const)
    .map((family) => `${Math.round(discountFor(family, settings) * 100)}% off ${FAMILY_LABELS[family]}`)
    .join("; ");
  const missingRates = [...new Set(costs.flatMap((estimate) =>
    estimate.components.flatMap((component) => component.missing ? [component.missing] : []),
  ))];
  container.append(
    note(
      `Discounts applied: ${applied}. ` +
        (missingRates.length ? `Missing usage: ${missingRates.join("; ")}. ` : "") +
        `Per-call figures price Conversation PII at the first volume tier; the business case ` +
        `walks the tier ladder for its monthly volume. Audio estimates multiply duration by ` +
        `channel count. Hosting, storage, logging, Fabric capacity, and Voice Live host-model ` +
        `charges are excluded.`,
    ),
    renderPricingSources(),
  );
  return container;
}

function renderParticipantWer(
  entries: Array<[string, EngineResult]>,
): HTMLDetailsElement | null {
  const rows = entries.flatMap(([, entry]) =>
    Object.entries(entry.metrics?.participants ?? {}).map(
      ([participant, metrics]) => ({
        label: entry.label,
        participant,
        wer: metrics.wer,
      }),
    ),
  );
  if (rows.length === 0) return null;

  const bestByParticipant = new Map<string, number | null>();
  for (const participant of new Set(rows.map((row) => row.participant))) {
    bestByParticipant.set(
      participant,
      bestValue(
        rows.filter((row) => row.participant === participant).map((row) => row.wer),
        "min",
      ),
    );
  }

  const details = document.createElement("details");
  details.className = "comparison-details";
  const summary = document.createElement("summary");
  summary.textContent = "Per-participant WER";
  details.append(
    summary,
    table(
      "<thead><tr><th>Architecture</th><th>Participant</th><th>WER</th></tr></thead>" +
        `<tbody>${rows.map((row) =>
          `<tr>${labelCell(row.label)}<td>${escapeHtml(row.participant)}</td>${winnerCell(
            formatPercent(row.wer),
            row.wer,
            bestByParticipant.get(row.participant) ?? null,
          )}</tr>`
        ).join("")}</tbody>`,
    ),
  );
  return details;
}

function renderSttComparison(
  report: BenchmarkReport,
  entries: Array<[string, EngineResult]>,
): HTMLElement {
  const container = section("STT accuracy and latency");

  const successful = entries.filter(([, entry]) => !entry.error && entry.metrics);
  const bestWer = bestValue(successful.map(([, entry]) => entry.metrics?.wer), "min");
  const bestAccuracy = bestValue(
    successful.map(([, entry]) => entry.metrics?.accuracy),
    "max",
  );
  const primaryLatency = (entry: EngineResult): number | null => {
    if (!entry.metrics) return null;
    return entry.metrics.finalization_lag.mean ?? entry.metrics.turnaround_seconds ?? null;
  };
  const bestLatency = bestValue(
    successful.map(([, entry]) => primaryLatency(entry)),
    "min",
  );
  const bestP95 = bestValue(
    successful.map(([, entry]) => entry.metrics?.finalization_lag.p95),
    "min",
  );
  const bestTranscriptReady = bestValue(
    successful.map(([, entry]) => entry.metrics?.time_to_full_transcript),
    "min",
  );

  container.append(
    table(`
    <thead>
      <tr>
        <th>Architecture</th><th>WER</th><th>Accuracy</th>
        <th>Mean lag</th><th>p95 lag</th>
        <th title="Seconds from the start of the call until the full transcript exists">
          Transcript ready
        </th>
        <th>Segments</th>
      </tr>
    </thead>
    <tbody>${entries.map(([, entry]) => {
      if (entry.error || !entry.metrics) {
        return `<tr>
          ${labelCell(entry.label)}
          <td colspan="6" class="numeric">failed: ${escapeHtml(entry.error ?? "no metrics")}</td>
        </tr>`;
      }
      const metrics = entry.metrics;
      const lag = metrics.finalization_lag;
      const latencyValue = primaryLatency(entry);
      const latency = lag.mean === null
        ? `${formatSeconds(metrics.turnaround_seconds)} (batch)`
        : formatSeconds(lag.mean);
      return `<tr>
        ${labelCell(entry.label)}
        ${winnerCell(formatPercent(metrics.wer), metrics.wer, bestWer)}
        ${winnerCell(formatPercent(metrics.accuracy), metrics.accuracy, bestAccuracy)}
        ${winnerCell(latency, latencyValue, bestLatency)}
        ${winnerCell(formatSeconds(lag.p95), lag.p95, bestP95)}
        ${winnerCell(
          `${metrics.time_to_full_transcript.toFixed(1)}s`,
          metrics.time_to_full_transcript,
          bestTranscriptReady,
        )}
        <td class="numeric">${metrics.segments}</td>
      </tr>`;
    }).join("")}</tbody>`),
  );

  const explanation = document.createElement("p");
  explanation.className = "section-note";
  explanation.innerHTML =
    `<strong>Transcript ready</strong> is measured from the start of the call. Both engines ` +
    `transcribe as the call happens, so both land within about a second of the caller hanging up. ` +
    (report.scored
      ? `Scored against ${report.reference_words} reference words · ${report.vad_utterances} VAD utterances · ${formatDuration(report.audio_seconds)} of audio.`
      : `No reference transcript was uploaded, so WER is not scored. Latency and transcripts are still measured.`);
  container.append(explanation);

  const participantWer = renderParticipantWer(entries);
  if (participantWer) container.append(participantWer);
  return container;
}

function renderPiiAccuracy(report: BenchmarkReport): HTMLElement {
  const container = section("PII redaction accuracy");

  const entries = piiEntries(report);
  if (entries.length === 0) {
    container.append(
      note(
        "PII accuracy not scored. This report has no architecture-independent ground-truth annotations.",
      ),
    );
    return container;
  }

  const bestPrecision = bestValue(entries.map(([, metrics]) => metrics.precision), "max");
  const bestRecall = bestValue(entries.map(([, metrics]) => metrics.recall), "max");
  const bestF1 = bestValue(entries.map(([, metrics]) => metrics.f1), "max");
  const bestCategory = bestValue(
    entries.map(([, metrics]) => metrics.category_accuracy),
    "max",
  );
  const bestLeakage = bestValue(
    entries.map(([, metrics]) => metrics.pii_leakage_rate),
    "min",
  );
  const bestAlignment = bestValue(
    entries.map(([, metrics]) => metrics.alignment_rate),
    "max",
  );
  container.append(
    table(`
    <thead><tr><th>Architecture</th><th>Precision</th><th>Recall</th><th>F1</th><th>Category accuracy</th><th>PII leakage</th><th>Alignment</th><th>TP / FP / FN</th></tr></thead>
    <tbody>${entries.map(([architectureId, metrics]) =>
      `<tr>${labelCell(architectureLabel(report, architectureId))}
        ${winnerCell(formatPercent(metrics.precision), metrics.precision, bestPrecision)}
        ${winnerCell(formatPercent(metrics.recall), metrics.recall, bestRecall)}
        ${winnerCell(formatPercent(metrics.f1), metrics.f1, bestF1)}
        ${winnerCell(
          metrics.category_accuracy === null ? "—" : formatPercent(metrics.category_accuracy),
          metrics.category_accuracy,
          bestCategory,
        )}
        ${winnerCell(
          formatPercent(metrics.pii_leakage_rate),
          metrics.pii_leakage_rate,
          bestLeakage,
        )}
        ${winnerCell(
          `${formatPercent(metrics.alignment_rate)} (${metrics.expected_entities}/${metrics.ground_truth_entities})`,
          metrics.alignment_rate,
          bestAlignment,
        )}
        <td class="numeric">${metrics.true_positives} / ${metrics.false_positives} / ${metrics.false_negatives}</td>
      </tr>`
    ).join("")}</tbody>`),
    note(
      "Exact source-turn spans determine precision, recall, F1, and leakage. Category accuracy is measured on matched spans; alignment excludes reference entities lost or changed by STT. Only the current-state architecture returns a redacted transcript, so it is the only one scored here — the modernized path is scored on what it does return, a summary that never contained the PII in the first place.",
    ),
  );
  return container;
}

export function describeUpload(upload: UploadMeta): string {
  const parts: string[] = [];
  if (upload.audio) {
    const { filename, duration_seconds, sample_rate, channels, size_bytes, transcoded } =
      upload.audio;
    const channelMap = upload.channel_map
      ? ` · ${Object.entries(upload.channel_map)
          .map(([channel, participant]) => `ch${channel}=${participant}`)
          .join(", ")}`
      : "";
    parts.push(
      `audio: ${filename ?? "recording"} · ${formatDuration(duration_seconds)} · ` +
        `${(sample_rate / 1000).toFixed(1)} kHz ${channels === 2 ? "stereo" : "mono"} · ` +
        `${formatBytes(size_bytes)}${channelMap}` +
        (transcoded ? " · transcoded" : ""),
    );
  }
  if (upload.transcript) {
    const { filename, lines, characters } = upload.transcript;
    parts.push(
      `transcript: ${filename ?? "transcript.txt"} · ${lines} lines · ` +
        `${characters.toLocaleString()} chars`,
    );
  }
  if (upload.pii_ground_truth) {
    const { filename, entities } = upload.pii_ground_truth;
    parts.push(`PII ground truth: ${filename ?? "annotations.json"} · ${entities} entities`);
  }
  return parts.join("  |  ");
}

export interface UploadHandlers {
  onDelete(uploadId: string, button: HTMLButtonElement): void;
}

export function renderUploads(
  panel: HTMLElement,
  uploads: UploadMeta[],
  handlers: UploadHandlers,
): void {
  if (uploads.length === 0) {
    panel.innerHTML =
      `<p class="empty">Nothing uploaded yet — a run with no attachments uses the built-in call.</p>`;
    return;
  }

  panel.replaceChildren(
    ...uploads.map((upload) => {
      const row = document.createElement("div");
      row.className = "row";

      const main = document.createElement("div");
      main.className = "row-main";
      const description = describeUpload(upload);
      const lines = description
        ? description.split("  |  ").map((part) => `<span>${escapeHtml(part)}</span>`).join("")
        : "empty upload";
      main.innerHTML = `
        <div class="row-title">${escapeHtml(upload.label ?? upload.id)}</div>
        <div class="row-meta">${lines}</div>
        <div class="badges">
          ${upload.builtin ? `<span class="badge builtin">default</span>` : ""}
          <span class="badge ${upload.audio ? "done" : "pending"}">${
            upload.audio ? "audio" : "no audio"
          }</span>
          <span class="badge ${upload.transcript ? "done" : "pending"}">${
            upload.transcript ? "reference transcript" : "no reference"
          }</span>
          <span class="badge ${upload.pii_ground_truth ? "done" : "pending"}">${
            upload.pii_ground_truth ? "PII ground truth" : "PII unscored"
          }</span>
        </div>`;

      const actions = document.createElement("div");
      actions.className = "actions";

      if (!upload.builtin) {
        const remove = document.createElement("button");
        remove.className = "danger";
        remove.textContent = "Delete";
        remove.addEventListener("click", () => handlers.onDelete(upload.id, remove));
        actions.append(remove);
      }

      row.append(main, actions);
      return row;
    }),
  );
}

export function renderMetricsTable(
  report: BenchmarkReport,
  settings: PricingSettings = DEFAULT_SETTINGS,
): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.className = "report";
  const entries = engineEntries(report);
  wrapper.append(
    renderScorecard(report, settings),
    renderArchitectureResults(report),
    renderSttComparison(report, entries),
    renderPiiAccuracy(report),
    renderPricing(report, settings),
  );

  const outputs = document.createElement("section");
  outputs.className = "outputs-group";
  const outputsHeading = document.createElement("h3");
  outputsHeading.textContent = "Engine transcripts";
  outputs.append(outputsHeading);
  let hasOutputs = false;

  for (const [, entry] of entries) {
    if (!entry.transcript && !entry.conversation) continue;
    hasOutputs = true;
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `Speaker turns — ${entry.label}`;
    const pre = document.createElement("pre");
    pre.textContent = entry.conversation
      ? entry.conversation.conversationItems
          .map((turn) => {
            const start = (turn.offset / 10_000_000).toFixed(2);
            return `[${start}s] ${turn.participantId}: ${turn.text}`;
          })
          .join("\n")
      : entry.transcript ?? "";
    details.append(summary, pre);
    outputs.append(details);

    if (entry.transcript) {
      const flat = document.createElement("details");
      const flatSummary = document.createElement("summary");
      flatSummary.textContent = `Flat transcript — ${entry.label}`;
      const flatPre = document.createElement("pre");
      flatPre.textContent = entry.transcript;
      flat.append(flatSummary, flatPre);
      outputs.append(flat);
    }
  }

  if (hasOutputs) wrapper.append(outputs);
  return wrapper;
}

export function renderCachedBenchmark(
  panel: HTMLElement,
  report: BenchmarkReport,
  settings: PricingSettings = DEFAULT_SETTINGS,
): void {
  panel.replaceChildren(renderMetricsTable(report, settings));
}

export function renderJobs(
  panel: HTMLElement,
  jobs: Job[],
  now = Date.now(),
  settings: PricingSettings = DEFAULT_SETTINGS,
): void {
  if (jobs.length === 0) {
    panel.innerHTML = `<p class="empty">No benchmark runs yet.</p>`;
    return;
  }

  const openDetails = new Set(
    [...panel.querySelectorAll<HTMLElement>(".job[data-job-id] details[open]")]
      .map((details) => {
        const jobId = details.closest<HTMLElement>(".job")?.dataset.jobId;
        const label = details.querySelector("summary")?.textContent;
        return jobId && label ? `${jobId}:${label}` : null;
      })
      .filter((value): value is string => value !== null),
  );

  panel.replaceChildren(
    ...jobs.map((job) => {
      const card = document.createElement("div");
      card.className = "job";
      card.dataset.jobId = job.id;
      if (job.status === "queued" || job.status === "running") {
        card.setAttribute("aria-busy", "true");
      }

      const engineBadges = Object.entries(job.engines)
        .filter(([key]) => isKnownEngine(key))
        .map(
          ([key, state]) =>
            `<span class="badge ${escapeHtml(state)}">${escapeHtml(
              job.engine_labels[key] ?? key,
            )}: ${escapeHtml(state)}</span>`,
        )
        .join("");

      const started = job.started_at ? new Date(job.started_at) : null;
      const finished = job.finished_at ? new Date(job.finished_at) : null;
      let elapsed = "";
      if (started && finished) {
        elapsed = ` · took ${formatDuration(
          (finished.getTime() - started.getTime()) / 1000,
        )}`;
      } else if (started) {
        elapsed = ` · running for ${formatDuration(
          (now - started.getTime()) / 1000,
        )}`;
      }

      const header = document.createElement("div");
      header.className = "job__head";
      header.innerHTML = `
        <div class="row-main">
          <div class="row-title job__id">
            ${escapeHtml(job.id)}
            <span class="badge ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
          </div>
          <div class="row-meta">upload ${escapeHtml(job.upload_id)}${escapeHtml(elapsed)}</div>
        </div>
        <div class="badges">${engineBadges}</div>`;
      card.append(header);

      if (job.status === "failed" && job.error) {
        const error = document.createElement("p");
        error.className = "message error";
        error.textContent = job.error;
        card.append(error);
      }

      if (job.status === "succeeded" && job.result) {
        card.append(renderMetricsTable(job.result, settings));
      } else if (job.status === "running") {
        const progress = document.createElement("div");
        progress.className = "progress";
        card.append(
          progress,
          note(
            "Real-time engines stream the call at 1x, so this takes about as long " +
              "as the recording.",
          ),
        );
      }

      for (const details of card.querySelectorAll("details")) {
        const label = details.querySelector("summary")?.textContent;
        if (label && openDetails.has(`${job.id}:${label}`)) {
          details.open = true;
        }
      }
      return card;
    }),
  );
}

/** Methodology caveats, so a technical reader can weigh every figure on this site. */
export function renderCaveats(): HTMLElement {
  const container = section("How to read these numbers", "comparison-section");
  const list = document.createElement("ul");
  list.className = "caveat-list";
  list.innerHTML = [
    "The audio is synthesized, with no overlapping speech, crosstalk, or line noise. Absolute error rates and lags are better here than they will be on real recordings; the relative comparison between the two stacks is the part that carries over.",
    "Unit prices are Azure list price in East US, before the discounts entered on the business case. Conversation PII tiers and the cheapest valid Standard, 3M, or 10M summarization plan are modelled; Foundry reserved capacity is represented only through the effective discount input.",
    "Stereo audio is submitted as two independent mono channels, so audio-hour costs multiply by channel count. Whether Azure bills that as one call-hour or two processed hours is unverified; two is the conservative planning bound.",
    `Only ${PROFILES[ARCHITECTURE_ORDER[0]]!.name} returns a redacted transcript, so it is the only architecture with transcript-level PII scores. Alignment rate shows how many reference annotations survived transcription at all — it separates STT loss from redaction loss.`,
    "Monthly and annual projections scale one measured call linearly by volume and average handle time. Real call mixes vary in length and content.",
  ]
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("");
  container.append(list);
  return container;
}
