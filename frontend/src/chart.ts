/**
 * Annual run-rate comparison: two horizontal stacked bars, segmented by the
 * contract line each dollar is billed against.
 *
 * Colors are the validated three-slot categorical set for this app's dark chart
 * surface (blue / orange / aqua). They encode the service family, not the
 * architecture, so the same hue means the same contract line in both bars — which
 * is what makes it obvious that the transcription line is the one that shrinks.
 */
import { escapeHtml, formatCompactMoney, formatMoney } from "./format.ts";
import { FAMILY_LABELS, type ServiceFamily } from "./pricing.ts";
import type { ArchitectureProjection, Projection } from "./pricing.ts";

const FAMILY_COLORS: Record<ServiceFamily, string> = {
  speech: "var(--series-1)",
  language: "var(--series-2)",
  llm: "var(--series-3)",
};

const WIDTH = 960;
const LABEL_WIDTH = 104;
/** Room to the right of the longest bar for its always-outside value label. */
const VALUE_GUTTER = 104;
const BAR_HEIGHT = 26;
const ROW_HEIGHT = 62;
const TOP = 10;
const AXIS_HEIGHT = 30;
const SEGMENT_GAP = 2;
const RADIUS = 4;

/** Rounded only on the outer end of the bar, square where segments meet. */
function segmentPath(
  x: number,
  y: number,
  width: number,
  height: number,
  roundLeft: boolean,
  roundRight: boolean,
): string {
  const r = Math.min(RADIUS, width / 2, height / 2);
  if (r <= 0 || (!roundLeft && !roundRight)) {
    return `M${x},${y} h${width} v${height} h${-width} Z`;
  }
  const left = roundLeft ? r : 0;
  const right = roundRight ? r : 0;
  return [
    `M${x + left},${y}`,
    `h${width - left - right}`,
    right ? `a${right},${right} 0 0 1 ${right},${right}` : "",
    `v${height - right * 2}`,
    right ? `a${right},${right} 0 0 1 ${-right},${right}` : "",
    `h${-(width - left - right)}`,
    left ? `a${left},${left} 0 0 1 ${-left},${-left}` : "",
    `v${-(height - left * 2)}`,
    left ? `a${left},${left} 0 0 1 ${left},${-left}` : "",
    "Z",
  ].join(" ");
}

function row(
  entry: ArchitectureProjection,
  index: number,
  scale: number,
): string {
  const y = TOP + index * ROW_HEIGHT;
  const barY = y + 18;
  const segments = entry.families.filter((family) => family.netCost > 0);
  const total = entry.annualNet;

  let cursor = LABEL_WIDTH;
  const marks = segments.map((family, position) => {
    const full = family.netCost * scale;
    const isFirst = position === 0;
    const isLast = position === segments.length - 1;
    // The gap comes out of the segment, so the bar's total length stays truthful.
    // A segment too small to survive the gap keeps its real width instead of
    // vanishing — a 0.5% line should read as a sliver, not as nothing.
    const gap = isLast || full <= SEGMENT_GAP * 2 ? 0 : SEGMENT_GAP;
    const width = Math.max(0, full - gap);
    const path = segmentPath(cursor, barY, width, BAR_HEIGHT, isFirst, isLast);
    cursor += full;
    const share = total > 0 ? (family.netCost / total) * 100 : 0;
    return `<path class="chart__segment" d="${path}" fill="${FAMILY_COLORS[family.family]}"
      ><title>${escapeHtml(entry.label)} — ${escapeHtml(FAMILY_LABELS[family.family])}: ${formatMoney(family.netCost)} per year (${share.toFixed(0)}%)</title></path>`;
  });

  // The value always sits outside the bar: inside-the-bar placement collides with
  // whichever segment happens to end there once the seller changes a discount.
  const barEnd = LABEL_WIDTH + total * scale;
  return `
    <g class="chart__row">
      <text class="chart__name" x="0" y="${y + 12}">${escapeHtml(
        entry.kind === "legacy" ? "Today" : "Modernized",
      )}</text>
      ${marks.join("")}
      <text class="chart__value" x="${barEnd + 10}" y="${barY + BAR_HEIGHT / 2 + 5}">${formatMoney(
        total,
      )}</text>
    </g>`;
}

export function renderCostChart(projection: Projection): HTMLElement {
  const figure = document.createElement("figure");
  figure.className = "chart";

  const entries = projection.architectures;
  const max = Math.max(...entries.map((entry) => entry.annualNet), 1);
  const plotWidth = WIDTH - LABEL_WIDTH - VALUE_GUTTER;
  const scale = plotWidth / max;
  const height = TOP + entries.length * ROW_HEIGHT + AXIS_HEIGHT;

  const families = [...new Set(entries.flatMap((entry) =>
    entry.families.filter((family) => family.netCost > 0).map((family) => family.family),
  ))];

  const caption = document.createElement("figcaption");
  caption.className = "chart__caption";
  caption.innerHTML =
    `<span class="chart__title">Annual cost at ${projection.settings.monthlyCalls.toLocaleString()} calls a month</span>` +
    `<span class="chart__subtitle">After your discounts, split by the contract line each dollar bills against</span>`;

  const legend = document.createElement("ul");
  legend.className = "chart__legend";
  legend.innerHTML = families
    .map(
      (family) =>
        `<li><span class="chart__swatch" style="background:${FAMILY_COLORS[family]}"></span>${escapeHtml(
          FAMILY_LABELS[family],
        )}</li>`,
    )
    .join("");

  const plot = document.createElement("div");
  plot.className = "chart__plot";
  plot.innerHTML = `
    <svg viewBox="0 0 ${WIDTH} ${height}" role="img"
         aria-label="Annual cost comparison. ${entries
           .map((entry) => `${entry.kind === "legacy" ? "Today" : "Modernized"}: ${formatMoney(entry.annualNet)}`)
           .join(". ")}.">
      ${entries.map((entry, index) => row(entry, index, scale)).join("")}
      <g class="chart__axis">
        <line x1="${LABEL_WIDTH}" y1="${height - AXIS_HEIGHT + 4}" x2="${LABEL_WIDTH + plotWidth}" y2="${height - AXIS_HEIGHT + 4}" />
        <text x="${LABEL_WIDTH}" y="${height - AXIS_HEIGHT + 20}">$0</text>
        <text x="${LABEL_WIDTH + plotWidth}" y="${height - AXIS_HEIGHT + 20}" text-anchor="end">${formatCompactMoney(max)}</text>
      </g>
    </svg>`;

  figure.append(caption, legend, plot);
  return figure;
}
