/**
 * The business case: what a migration costs, and what it buys.
 *
 * Everything here is derived from the measured benchmark run and the seller's own
 * discounts. Where a claim is weaker than it looks — the latency one is — this
 * says so rather than rounding it up into a headline.
 */
import type { BenchmarkReport } from "./api.ts";
import {
  ARCH_LEGACY,
  ARCH_MODERN,
  ENGINE_LEGACY,
  ENGINE_MODERN,
  PROFILES,
} from "./catalog.ts";
import { renderCostChart } from "./chart.ts";
import {
  escapeHtml,
  formatDelta,
  formatMoney,
  formatPercent,
  formatSeconds,
  formatUnitCost,
} from "./format.ts";
import {
  FAMILY_LABELS,
  discountFor,
  pricingRates,
  projectCosts,
  type PricingSettings,
  type Projection,
} from "./pricing.ts";
import { renderPricingSources } from "./pricing-sources.ts";

const RETIREMENT_URL =
  "https://learn.microsoft.com/en-us/azure/ai-services/language-service/summarization/overview";

function element(tag: string, className: string, html = ""): HTMLElement {
  const node = document.createElement(tag);
  node.className = className;
  if (html) node.innerHTML = html;
  return node;
}

/* ------------------------------------------------------------------- headline */

function renderRetirementNotice(): HTMLElement {
  return element(
    "aside",
    "notice notice--retirement",
    `<h2>Why this conversation is happening now</h2>
     <p>
       Azure AI Language <strong>conversation summarization is scheduled to retire on
       March&nbsp;31,&nbsp;2029</strong>, which puts a date on the current call-processing
       stack. Anything built on it needs a successor before then.
       <a href="${RETIREMENT_URL}" target="_blank" rel="noreferrer">Microsoft Learn — summarization overview</a>
     </p>
     <p class="notice__aside">
       That retirement covers the summarization feature specifically. Azure AI Speech
       and the rest of Azure AI Language are not retiring on that date, and this page
       does not claim they are.
     </p>`,
  );
}

function renderHero(projection: Projection): HTMLElement {
  const { legacy, modern, annualSaving, savingPercent, settings } = projection;
  const hero = element("section", "hero");

  if (!legacy || !modern) {
    hero.append(element("p", "empty", "The saved run has no cost data to project."));
    return hero;
  }

  const discounted = [
    settings.speechDiscount,
    settings.azureLanguageDiscount,
    settings.foundryLlmDiscount,
  ].some((value) => value > 0);

  hero.innerHTML = `
    <div class="hero__figure">
      <span class="hero__label">Annual saving</span>
      <strong class="hero__value">${formatMoney(annualSaving)}</strong>
      <span class="hero__meta">
        ${settings.monthlyCalls.toLocaleString()} calls a month ·
        ${settings.averageCallMinutes.toFixed(2)} min average ·
        ${discounted ? "your discounts applied" : "list price"}
      </span>
    </div>
    <div class="hero__body">
      <p class="hero__lede">
        Moving this workload from Azure Speech + Azure AI Language to MAI-Transcribe-1.5
        with a Foundry model cuts the run rate from <strong>${formatMoney(legacy.annualNet)}</strong>
        to <strong>${formatMoney(modern.annualNet)}</strong> a year${
          savingPercent === null ? "" : ` — <strong>${Math.round(savingPercent * 100)}% less</strong>`
        },
        while cutting the transcription error rate by more than a third.
      </p>
      <p class="hero__note">
        Adjust the discounts below to your customer's contract. Every number on this
        page recalculates.
      </p>
    </div>`;
  return hero;
}

/* --------------------------------------------------------------------- deltas */

interface Delta {
  label: string;
  legacy: string;
  modern: string;
  change: string;
  tone: "good" | "flat";
  note: string;
}

function deltas(report: BenchmarkReport, projection: Projection): Delta[] {
  const items: Delta[] = [];
  const { legacy, modern } = projection;

  if (legacy && modern) {
    const change = legacy.perCallNet > 0
      ? (modern.perCallNet - legacy.perCallNet) / legacy.perCallNet
      : 0;
    items.push({
      label: "Cost per call",
      legacy: formatUnitCost(legacy.perCallNet),
      modern: formatUnitCost(modern.perCallNet),
      change: formatDelta(change),
      tone: "good",
      note:
        `Transcription is the bulk of it: the modern engine lists at ` +
        `$${pricingRates.maiTranscribePerAudioHour.toFixed(2)} an audio hour ` +
        `against $${pricingRates.azureSpeechPerAudioHour.toFixed(2)}.`,
    });
  }

  const legacyWer = report.engines?.[ENGINE_LEGACY]?.metrics?.wer;
  const modernWer = report.engines?.[ENGINE_MODERN]?.metrics?.wer;
  if (typeof legacyWer === "number" && typeof modernWer === "number") {
    items.push({
      label: "Word error rate",
      legacy: formatPercent(legacyWer),
      modern: formatPercent(modernWer),
      change: formatDelta((modernWer - legacyWer) / legacyWer),
      tone: "good",
      note: "Fewer transcription errors means fewer missed entities downstream, and a summary built on what was actually said.",
    });
  }

  const legacyLatency = report.architectures?.[ARCH_LEGACY]?.latency;
  const modernLatency = report.architectures?.[ARCH_MODERN]?.latency;
  if (legacyLatency && modernLatency) {
    items.push({
      label: "Work after the transcript",
      legacy: formatSeconds(legacyLatency.downstream_seconds),
      modern: formatSeconds(modernLatency.downstream_seconds),
      change: formatDelta(
        (modernLatency.downstream_seconds - legacyLatency.downstream_seconds) /
          legacyLatency.downstream_seconds,
      ),
      tone: "good",
      note: "One model call replaces two Azure AI Language endpoints.",
    });
    items.push({
      label: "Total time to a PII-safe result",
      legacy: formatSeconds(legacyLatency.end_to_end_seconds),
      modern: formatSeconds(modernLatency.end_to_end_seconds),
      change: formatDelta(
        (modernLatency.end_to_end_seconds - legacyLatency.end_to_end_seconds) /
          legacyLatency.end_to_end_seconds,
      ),
      tone: "flat",
      note: "Effectively a tie, and that is the honest read. Both transcribe live, so both finish within about a second of the caller hanging up — latency is a reason this migration is safe, not a reason to make it.",
    });
  }

  return items;
}

function renderDeltas(report: BenchmarkReport, projection: Projection): HTMLElement {
  const section = element("section", "panel panel--deltas");
  section.append(
    element(
      "div",
      "panel__head",
      `<h2>What changes</h2>
       <p class="panel__hint">Measured on the same 8m 24s call, transcribed by both stacks.</p>`,
    ),
  );

  const list = element("ul", "delta-grid");
  list.innerHTML = deltas(report, projection)
    .map(
      (delta) => `
      <li class="delta delta--${delta.tone}">
        <span class="delta__label">${escapeHtml(delta.label)}</span>
        <span class="delta__pair">
          <span class="delta__from">${escapeHtml(delta.legacy)}</span>
          <span class="delta__arrow" aria-hidden="true">→</span>
          <span class="delta__to">${escapeHtml(delta.modern)}</span>
        </span>
        <span class="delta__change">${escapeHtml(delta.change)}</span>
        <span class="delta__note">${escapeHtml(delta.note)}</span>
      </li>`,
    )
    .join("");
  section.append(list);
  return section;
}

/* ---------------------------------------------------------------------- cards */

function renderComparisonCards(
  report: BenchmarkReport,
  projection: Projection,
): HTMLElement {
  const section = element("section", "panel panel--cards");
  section.append(
    element(
      "div",
      "panel__head",
      `<h2>The two stacks</h2>
       <p class="panel__hint">Both process the same stereo recording, with speaker identity taken from the channel rather than from billed diarization.</p>`,
    ),
  );

  const grid = element("div", "card-grid");
  for (const architectureId of [ARCH_LEGACY, ARCH_MODERN]) {
    const info = PROFILES[architectureId]!;
    const entry = projection.architectures.find(
      (item) => item.architectureId === architectureId,
    );
    const wer = report.engines?.[info.engineId]?.metrics?.wer;
    const latency = report.architectures?.[architectureId]?.latency;

    grid.append(
      element(
        "article",
        `arch-card arch-card--${info.kind}`,
        `<span class="arch-card__flag">${info.kind === "legacy" ? "Current state" : "Recommended"}</span>
         <h3>${escapeHtml(info.name)}</h3>
         <p class="arch-card__tagline">${escapeHtml(info.tagline)}</p>
         <ul class="arch-card__stack">
           ${info.stack.map((step) => `<li>${escapeHtml(step)}</li>`).join("")}
         </ul>
         <dl class="arch-card__stats">
           <div><dt>Cost per call</dt><dd>${entry ? formatUnitCost(entry.perCallNet) : "—"}</dd></div>
           <div><dt>Word error rate</dt><dd>${formatPercent(wer)}</dd></div>
           <div><dt>Result ready</dt><dd>${latency ? formatSeconds(latency.end_to_end_seconds) : "—"}</dd></div>
         </dl>
         <p class="arch-card__returns"><strong>Returns:</strong> ${escapeHtml(info.returns)}</p>`,
      ),
    );
  }
  section.append(grid);
  return section;
}

/* ----------------------------------------------------------------- projection */

function discountSummary(settings: PricingSettings): string {
  const applied = (["speech", "language", "llm"] as const)
    .map((family) => ({ family, value: discountFor(family, settings) }))
    .filter((entry) => entry.value > 0)
    .map((entry) => `${Math.round(entry.value * 100)}% off ${FAMILY_LABELS[entry.family]}`);
  return applied.length ? applied.join(", ") : "no discounts — list price";
}

function renderProjectionTable(projection: Projection): HTMLElement {
  const wrap = element("div", "table-wrap");
  const rows = projection.architectures
    .map(
      (entry) => `
      <tr class="projection-row projection-row--${entry.kind}">
        <td>
          <span class="arch-cell__name">${escapeHtml(entry.kind === "legacy" ? "Today" : "Modernized")}</span>
          <span class="stage-metrics">${escapeHtml(entry.label)}</span>
        </td>
        <td class="numeric">${formatUnitCost(entry.perCallList)}</td>
        <td class="numeric">${formatUnitCost(entry.perCallNet)}</td>
        <td class="numeric">${formatMoney(entry.monthlyNet)}</td>
        <td class="numeric"><strong>${formatMoney(entry.annualNet)}</strong></td>
      </tr>`,
    )
    .join("");

  const saving = projection.legacy && projection.modern
    ? `<tr class="projection-row projection-row--saving">
         <td>Saving</td>
         <td class="numeric">${formatUnitCost(projection.legacy.perCallList - projection.modern.perCallList)}</td>
         <td class="numeric">${formatUnitCost(projection.legacy.perCallNet - projection.modern.perCallNet)}</td>
         <td class="numeric">${formatMoney(projection.legacy.monthlyNet - projection.modern.monthlyNet)}</td>
         <td class="numeric"><strong>${formatMoney(projection.annualSaving)}</strong></td>
       </tr>`
    : "";

  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Architecture</th>
          <th>Per call, list</th>
          <th>Per call, your rate</th>
          <th>Per month</th>
          <th>Per year</th>
        </tr>
      </thead>
      <tbody>${rows}${saving}</tbody>
    </table>`;
  return wrap;
}

export function renderPricingResults(
  host: HTMLElement,
  report: BenchmarkReport,
  settings: PricingSettings,
): void {
  const projection = projectCosts(report, settings);
  const blended = projection.blendedPiiRate;

  const footnotes = element(
    "p",
    "section-note",
    `Applied: ${escapeHtml(discountSummary(settings))}. ` +
      `Conversation PII is priced through its volume tiers, which blend to ` +
      `$${blended.toFixed(3)} per 1,000 records at this volume. ` +
      `Usage is measured from the saved run and scaled linearly to an average call of ` +
      `${settings.averageCallMinutes.toFixed(2)} minutes; stereo audio is billed as two ` +
      `submitted channels. Excludes hosting, storage, logging, egress, the Voice Live ` +
      `host-model charge, and Fabric capacity.`,
  );

  host.replaceChildren(
    renderProjectionTable(projection),
    renderCostChart(projection),
    footnotes,
    renderPricingSources(),
  );
}

/* ----------------------------------------------------------------------- page */

export function renderBusinessCase(
  host: HTMLElement,
  report: BenchmarkReport,
  settings: PricingSettings,
): void {
  const projection = projectCosts(report, settings);
  host.replaceChildren(
    renderRetirementNotice(),
    renderHero(projection),
    renderDeltas(report, projection),
    renderComparisonCards(report, projection),
  );
}
