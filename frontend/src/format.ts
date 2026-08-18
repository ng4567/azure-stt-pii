/** Shared formatting. Every number the UI shows goes through one of these. */

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  return minutes > 0 ? `${minutes}m ${total % 60}s` : `${total}s`;
}

export function formatPercent(value: number | undefined): string {
  return value === undefined ? "—" : `${(value * 100).toFixed(2)}%`;
}

export function formatSeconds(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(2)}s`;
}

/** Fractional dollars, for a single call. */
export function formatUnitCost(value: number): string {
  return `$${value.toFixed(6)}`;
}

/** Whole dollars, for monthly and annual totals. */
export function formatMoney(value: number): string {
  const rounded = Math.round(value);
  const sign = rounded < 0 ? "-" : "";
  return `${sign}$${Math.abs(rounded).toLocaleString("en-US")}`;
}

/** Compact dollars for chart axes and tiles: $1.2M, $226K, $940. */
export function formatCompactMoney(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

/**
 * A signed relative change, e.g. "−65%". A change that rounds to nothing is named
 * rather than printed as "−0%", which reads like a defect.
 */
export function formatDelta(fraction: number): string {
  const percent = Math.round(Math.abs(fraction) * 100);
  if (percent === 0) return "about even";
  return `${fraction < 0 ? "−" : "+"}${percent}%`;
}

/** Escape text before it goes into an innerHTML template. */
export function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
