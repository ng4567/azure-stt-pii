/** Pure rendering helpers: state in, DOM out. */
import type { EngineResult, Job, UploadMeta } from "./api.ts";

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

export function describeUpload(upload: UploadMeta): string {
  const parts: string[] = [];
  if (upload.audio) {
    const { filename, duration_seconds, sample_rate, size_bytes, transcoded } =
      upload.audio;
    parts.push(
      `audio: ${filename ?? "recording"} · ${formatDuration(duration_seconds)} · ` +
        `${(sample_rate / 1000).toFixed(1)} kHz mono · ${formatBytes(size_bytes)}` +
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
  return parts.join("  |  ");
}

export interface UploadHandlers {
  onBenchmark(uploadId: string, button: HTMLButtonElement): void;
  onDelete(uploadId: string, button: HTMLButtonElement): void;
}

export function renderUploads(
  panel: HTMLElement,
  uploads: UploadMeta[],
  handlers: UploadHandlers,
): void {
  if (uploads.length === 0) {
    panel.innerHTML = `<p class="empty">Nothing uploaded yet.</p>`;
    return;
  }

  panel.replaceChildren(
    ...uploads.map((upload) => {
      const row = document.createElement("div");
      row.className = "row";

      const main = document.createElement("div");
      main.className = "row-main";
      main.innerHTML = `
        <div class="row-title">${upload.id}</div>
        <div class="row-meta">${describeUpload(upload) || "empty upload"}</div>
        <div class="badges">
          <span class="badge ${upload.audio ? "done" : "pending"}">${
            upload.audio ? "audio" : "no audio"
          }</span>
          <span class="badge ${upload.transcript ? "done" : "pending"}">${
            upload.transcript ? "reference transcript" : "no reference"
          }</span>
        </div>`;

      const actions = document.createElement("div");
      actions.className = "actions";

      const run = document.createElement("button");
      run.textContent = upload.transcript
        ? "Transcribe + score"
        : "Transcribe (unscored)";
      run.disabled = !upload.audio;
      run.title = upload.audio
        ? "Run all three architectures against this recording"
        : "Upload audio to run the benchmark";
      run.addEventListener("click", () => handlers.onBenchmark(upload.id, run));

      const remove = document.createElement("button");
      remove.className = "danger";
      remove.textContent = "Delete";
      remove.addEventListener("click", () => handlers.onDelete(upload.id, remove));

      actions.append(run, remove);
      row.append(main, actions);
      return row;
    }),
  );
}

export function renderMetricsTable(
  report: NonNullable<Job["result"]>,
): HTMLElement {
  const wrapper = document.createElement("div");
  const entries = Object.entries(report.engines);

  const rows = entries
    .map(([, entry]: [string, EngineResult]) => {
      if (entry.error || !entry.metrics) {
        return `<tr>
            <td>${entry.label}</td>
            <td colspan="6" class="numeric">failed: ${entry.error ?? "no metrics"}</td>
          </tr>`;
      }
      const m = entry.metrics;
      const lag = m.finalization_lag;
      const latency =
        lag.mean === null
          ? `${formatSeconds(m.turnaround_seconds)} (batch)`
          : formatSeconds(lag.mean);
      return `<tr>
          <td>${entry.label}</td>
          <td class="numeric">${formatPercent(m.wer)}</td>
          <td class="numeric">${formatPercent(m.accuracy)}</td>
          <td class="numeric">${latency}</td>
          <td class="numeric">${formatSeconds(lag.p95)}</td>
          <td class="numeric">${m.time_to_full_transcript.toFixed(1)}s</td>
          <td class="numeric">${m.segments}</td>
        </tr>`;
    })
    .join("");

  wrapper.innerHTML = `
    <table>
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
      <tbody>${rows}</tbody>
    </table>
    <p class="row-meta">
      <strong>Transcript ready</strong> is measured from the start of the call:
      real-time engines overlap the call, batch cannot start until the caller hangs up.
    </p>
    ${
      report.scored
        ? `<p class="row-meta">Scored against ${report.reference_words} reference words ·
             ${report.vad_utterances} VAD utterances ·
             ${formatDuration(report.audio_seconds)} of audio.</p>`
        : `<p class="row-meta">No reference transcript was uploaded, so WER is not
             scored. Latency and transcripts are still measured.</p>`
    }`;

  for (const [, entry] of entries) {
    if (!entry.transcript) continue;
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `Transcript — ${entry.label}`;
    const pre = document.createElement("pre");
    pre.textContent = entry.transcript;
    details.append(summary, pre);
    wrapper.append(details);
  }

  return wrapper;
}

export function renderJobs(panel: HTMLElement, jobs: Job[], now = Date.now()): void {
  if (jobs.length === 0) {
    panel.innerHTML = `<p class="empty">No benchmark runs yet.</p>`;
    return;
  }

  panel.replaceChildren(
    ...jobs.map((job) => {
      const card = document.createElement("div");
      card.className = "job";

      const engineBadges = Object.entries(job.engines)
        .map(
          ([key, state]) =>
            `<span class="badge ${state}">${
              job.engine_labels[key] ?? key
            }: ${state}</span>`,
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
      header.innerHTML = `
        <div class="row-title">
          ${job.id}
          <span class="badge ${job.status}">${job.status}</span>
        </div>
        <div class="row-meta">upload ${job.upload_id}${elapsed}</div>
        <div class="badges">${engineBadges}</div>`;
      card.append(header);

      if (job.status === "failed" && job.error) {
        const error = document.createElement("p");
        error.className = "message error";
        error.textContent = job.error;
        card.append(error);
      }

      if (job.status === "succeeded" && job.result) {
        card.append(renderMetricsTable(job.result));
      } else if (job.status === "running") {
        const note = document.createElement("p");
        note.className = "row-meta";
        note.textContent =
          "Real-time engines stream the call at 1x, so this takes about as long " +
          "as the recording.";
        card.append(note);
      }

      return card;
    }),
  );
}
