/**
 * Which recording the whole app is reporting on.
 *
 * Every figure on every view — cost, WER, latency, transcripts — comes from one
 * benchmark report. That is the built-in sample call until someone runs their own,
 * and the app is explicit about which, because a price quoted from the wrong call is
 * worse than no price at all.
 */
import type { BenchmarkReport, Job, UploadMeta } from "./api.ts";
import { escapeHtml, formatDuration } from "./format.ts";

export const BUILTIN_SOURCE_ID = "builtin";

export interface CallSource {
  id: string;
  label: string;
  detail: string;
  builtin: boolean;
  /** Set for a user's own run, so the evidence view can fetch that recording. */
  uploadId?: string;
  report: BenchmarkReport;
}

function describe(report: BenchmarkReport): string {
  const channels = report.channel_count === 2 ? "stereo" : "mono";
  const scored = report.scored ? "scored against a reference" : "no reference transcript";
  return `${formatDuration(report.audio_seconds)} · ${channels} · ${scored}`;
}

function uploadName(upload: UploadMeta | undefined, job: Job): string {
  return (
    upload?.audio?.filename ??
    upload?.label ??
    upload?.id ??
    `run ${job.id}`
  );
}

/**
 * The built-in call first, then each completed run of an uploaded recording,
 * newest first. A run that failed or is still going has no report to show.
 */
export function collectSources(
  builtin: BenchmarkReport | null,
  jobs: Job[],
  uploads: UploadMeta[],
): CallSource[] {
  const sources: CallSource[] = [];
  if (builtin) {
    sources.push({
      id: BUILTIN_SOURCE_ID,
      label: "Built-in sample call",
      detail: describe(builtin),
      builtin: true,
      report: builtin,
    });
  }

  const byId = new Map(uploads.map((upload) => [upload.id, upload]));
  for (const job of jobs) {
    if (job.status !== "succeeded" || !job.result) continue;
    const upload = byId.get(job.upload_id);
    // Deleting an upload removes its durable artifacts, but the in-memory job
    // history can outlive it until the API restarts. Do not offer a source whose
    // recording and reference endpoints can no longer exist.
    if (!upload) continue;
    // The built-in upload re-run is still the built-in call, not a new one.
    const isBuiltinUpload = upload?.builtin === true;
    sources.push({
      id: job.id,
      label: isBuiltinUpload
        ? `Built-in sample call · re-run ${job.id.slice(0, 6)}`
        : `Test call — ${uploadName(upload, job)}`,
      detail: describe(job.result),
      builtin: isBuiltinUpload,
      uploadId: job.upload_id,
      report: job.result,
    });
  }
  return sources;
}

export interface SourceBarHandlers {
  onSelect(sourceId: string): void;
  onUploadRequest(): void;
}

/**
 * The bar is rebuilt whenever the run list changes, so it must not steal focus or
 * clobber a selection the user just made — hence `selectedId` comes in from outside.
 */
export function renderSourceBar(
  host: HTMLElement,
  sources: CallSource[],
  selectedId: string,
  handlers: SourceBarHandlers,
): void {
  const active = sources.find((source) => source.id === selectedId) ?? sources[0];
  const hasOwn = sources.some((source) => !source.builtin);

  host.innerHTML = `
    <div class="sourcebar__lead">
      <span class="sourcebar__eyebrow">Pricing this call</span>
      <span class="sourcebar__detail">${
        active ? escapeHtml(active.detail) : "no benchmark loaded yet"
      }</span>
    </div>
    <div class="sourcebar__controls">
      <label class="sourcebar__picker" for="source-select">
        <span class="sourcebar__picker-label">Recording</span>
        <select id="source-select"${sources.length < 2 ? " disabled" : ""}>
          ${sources
            .map(
              (source) =>
                `<option value="${escapeHtml(source.id)}"${
                  source.id === active?.id ? " selected" : ""
                }>${escapeHtml(source.label)}</option>`,
            )
            .join("")}
        </select>
      </label>
      <button type="button" id="source-upload" class="primary-ghost">
        ${hasOwn ? "Run another test call" : "Attach an approved test call"}
      </button>
    </div>`;

  host.querySelector<HTMLSelectElement>("#source-select")?.addEventListener("change", (event) => {
    handlers.onSelect((event.target as HTMLSelectElement).value);
  });
  host.querySelector<HTMLButtonElement>("#source-upload")?.addEventListener("click", () => {
    handlers.onUploadRequest();
  });
}

/** Shown on the business case so nobody quotes the sample call by accident. */
export function renderSourceNotice(
  host: HTMLElement,
  source: CallSource | undefined,
): void {
  if (!source || source.builtin) {
    host.replaceChildren();
    return;
  }

  const notice = document.createElement("aside");
  notice.className = "notice notice--own";
  notice.innerHTML = `
    <h2>Using the selected test recording</h2>
    <p>
      Every number below is measured from <strong>${escapeHtml(source.label.replace(/^Test call — /, ""))}</strong>
      — ${escapeHtml(source.detail)}. Switch back to the built-in sample any time
      from the recording picker above.
    </p>`;
  host.replaceChildren(notice);
}
