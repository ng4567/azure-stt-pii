/** Pure rendering helpers: state in, DOM out. */
import type {
  ArchitectureResult,
  ArchitectureStage,
  EngineResult,
  Job,
  UploadMeta,
} from "./api.ts";
import { estimateArchitectureCosts } from "./pricing.ts";

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

type WinnerDirection = "min" | "max";

function bestValue(
  values: Array<number | null | undefined>,
  direction: WinnerDirection,
): number | null {
  const finite = values.filter(
    (value): value is number => typeof value === "number" && Number.isFinite(value),
  );
  if (finite.length === 0) return null;
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

const STAGE_LABELS: Record<string, string> = {
  stt: "STT transcript ready",
  pii_endpoint: "Conversation PII endpoint",
  summarizer_endpoint: "Summarizer endpoint",
  regex_detection: "Regex candidate scan",
  request_preparation: "Backend request preparation + Entra auth",
  llm_api_call: "LLM API call (PII + summary)",
  response_validation: "Backend response validation",
  transcript_redaction: "Transcript entity redaction",
  summary_sanitization: "Summary entity redaction",
  pii_redaction: "PII redaction",
  summarization: "Summarization",
};
const ENGINE_ORDER = [
  "architecture-1-azure-speech-realtime",
  "architecture-2-mai-transcribe-realtime",
  "architecture-3-mai-transcribe-batch",
];
const ARCHITECTURE_ORDER = [
  "architecture-1-azure-language",
  "architecture-2-mai-realtime-deepseek",
  "architecture-3-mai-batch-deepseek",
];

function stageDuration(stageKey: string, stage: ArchitectureStage): number {
  if (stageKey === "stt") {
    const ready = stage.metrics.time_to_full_transcript;
    if (typeof ready === "number" && Number.isFinite(ready)) return ready;
  }
  return stage.wall_seconds;
}

function renderArchitectureResults(
  report: NonNullable<Job["result"]>,
): HTMLElement {
  const entries = Object.entries(report.architectures ?? {})
    .sort(
      ([left], [right]) =>
        ARCHITECTURE_ORDER.indexOf(left) - ARCHITECTURE_ORDER.indexOf(right),
    )
    .map(([, result]) => result);
  const section = document.createElement("section");
  section.className = "architecture-results";
  const heading = document.createElement("h3");
  heading.textContent = "End-to-end architecture latency";
  section.append(heading);

  if (entries.length === 0) {
    const unavailable = document.createElement("p");
    unavailable.className = "row-meta";
    unavailable.textContent =
      "This saved comparison predates the end-to-end pipeline timings. Start a benchmark to measure STT, downstream stages, and total latency for all three architectures.";
    section.append(unavailable);
    return section;
  }

  const table = document.createElement("table");
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
  table.innerHTML = `
    <thead><tr><th>Architecture</th><th>End to end</th><th>STT ready</th><th>Downstream</th></tr></thead>
    <tbody>${entries.map((result: ArchitectureResult) => {
      if (result.status === "failed" || !result.latency) {
        return `<tr><td>${result.label}</td><td colspan="3">failed: ${result.error ?? "unknown error"}</td></tr>`;
      }
      return `<tr>
        <td>${result.label}</td>
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
    }).join("")}</tbody>`;
  section.append(table);

  const note = document.createElement("p");
  note.className = "row-meta";
  note.textContent =
    "End to end runs from the start of the call until the sanitized summary and redacted transcript are ready. Parallel endpoint times overlap and are not added together.";
  section.append(note);

  for (const result of entries) {
    const details = document.createElement("details");
    details.className = "architecture-detail";
    const summary = document.createElement("summary");
    summary.textContent = `Pipeline stages and outputs — ${result.label}`;
    details.append(summary);

    if (result.status === "failed") {
      const error = document.createElement("p");
      error.className = "message error";
      error.textContent = result.error ?? "Architecture failed.";
      details.append(error);
      section.append(details);
      continue;
    }

    const stages = document.createElement("table");
    stages.innerHTML = `
      <thead><tr><th>Stage</th><th>Provider / model</th><th>Latency</th></tr></thead>
      <tbody>${Object.entries(result.stages).map(([key, stage]) => `
        <tr>
          <td>${STAGE_LABELS[key] ?? key.replaceAll("_", " ")}</td>
          <td>${stage.provider} · ${stage.model}</td>
          <td class="numeric">${formatSeconds(stageDuration(key, stage))}</td>
        </tr>`).join("")}</tbody>`;
    details.append(stages);

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
    section.append(details);
  }

  return section;
}

function renderPricing(report: NonNullable<Job["result"]>): HTMLElement {
  const section = document.createElement("section");
  section.className = "comparison-section pricing-results";
  const heading = document.createElement("h3");
  heading.textContent = "Estimated processing cost";
  section.append(heading);

  const costs = estimateArchitectureCosts(report);
  const complete = costs.filter((estimate) => estimate.complete);
  const bestList = bestValue(complete.map((estimate) => estimate.listTotal), "min");
  const bestDiscounted = bestValue(
    complete.map((estimate) => estimate.discountedTotal),
    "min",
  );
  const bestSavings = bestValue(
    complete.map((estimate) => estimate.listTotal - estimate.discountedTotal),
    "max",
  );
  const table = document.createElement("table");
  table.innerHTML = `
    <thead><tr><th>Architecture</th><th>Components</th><th>List total</th><th>Discounted total</th><th>Savings</th></tr></thead>
    <tbody>${costs.map((estimate) => {
      const savings = estimate.listTotal - estimate.discountedTotal;
      return `<tr>
        <td>${estimate.label}</td>
        <td>${estimate.components
          .map((component) => `${component.label}: ${component.usage} ($${component.listCost?.toFixed(4) ?? "—"} list → $${component.discountedCost?.toFixed(4) ?? "—"})`)
          .join("<br>")}</td>
        ${winnerCell(
          `$${estimate.listTotal.toFixed(4)}`,
          estimate.complete ? estimate.listTotal : null,
          bestList,
        )}
        ${winnerCell(
          `$${estimate.discountedTotal.toFixed(4)}`,
          estimate.complete ? estimate.discountedTotal : null,
          bestDiscounted,
        )}
        ${winnerCell(
          `$${savings.toFixed(4)}`,
          estimate.complete ? savings : null,
          bestSavings,
        )}
      </tr>`;
    }).join("")}</tbody>`;
  section.append(table);

  const missingRates = [...new Set(costs.flatMap((estimate) =>
    estimate.components.flatMap((component) => component.missing ? [component.missing] : []),
  ))];
  const note = document.createElement("p");
  note.className = "row-meta";
  note.textContent =
    `Applied discounts: 90% on Azure Speech and MAI-Transcribe; 70% on Conversation PII and summarization; no DeepSeek discount. ` +
    (missingRates.length ? `Missing usage: ${missingRates.join("; ")}. ` : "") +
    `Audio estimates multiply duration by channel count. DeepSeek cached-input pricing is configured but unused because cache usage is not reported. ` +
    `Hosting, storage, logging, and Voice Live host-model charges are excluded.`;
  section.append(note);
  return section;
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
  const table = document.createElement("table");
  table.innerHTML =
    "<thead><tr><th>Architecture</th><th>Participant</th><th>WER</th></tr></thead>" +
    `<tbody>${rows.map((row) =>
      `<tr><td>${row.label}</td><td>${row.participant}</td>${winnerCell(
        formatPercent(row.wer),
        row.wer,
        bestByParticipant.get(row.participant) ?? null,
      )}</tr>`
    ).join("")}</tbody>`;
  details.append(summary, table);
  return details;
}

function renderSttComparison(
  report: NonNullable<Job["result"]>,
  entries: Array<[string, EngineResult]>,
): HTMLElement {
  const section = document.createElement("section");
  section.className = "comparison-section";
  const heading = document.createElement("h3");
  heading.textContent = "STT accuracy and latency";
  section.append(heading);

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

  const table = document.createElement("table");
  table.innerHTML = `
    <thead>
      <tr>
        <th>Architecture</th><th>WER</th><th>Accuracy</th>
        <th>Primary latency</th><th>p95 lag</th>
        <th title="Seconds from the start of the call until the full transcript exists">
          Transcript ready
        </th>
        <th>Segments</th>
      </tr>
    </thead>
    <tbody>${entries.map(([, entry]) => {
      if (entry.error || !entry.metrics) {
        return `<tr>
          <td>${entry.label}</td>
          <td colspan="6" class="numeric">failed: ${entry.error ?? "no metrics"}</td>
        </tr>`;
      }
      const metrics = entry.metrics;
      const lag = metrics.finalization_lag;
      const latencyValue = primaryLatency(entry);
      const latency = lag.mean === null
        ? `${formatSeconds(metrics.turnaround_seconds)} (batch)`
        : formatSeconds(lag.mean);
      return `<tr>
        <td>${entry.label}</td>
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
    }).join("")}</tbody>`;
  section.append(table);

  const note = document.createElement("p");
  note.className = "row-meta";
  note.innerHTML =
    `<strong>Transcript ready</strong> is measured from the start of the call: ` +
    `real-time engines overlap the call, batch cannot start until the caller hangs up. ` +
    (report.scored
      ? `Scored against ${report.reference_words} reference words · ${report.vad_utterances} VAD utterances · ${formatDuration(report.audio_seconds)} of audio.`
      : `No reference transcript was uploaded, so WER is not scored. Latency and transcripts are still measured.`);
  section.append(note);
  return section;
}

function renderPiiAccuracy(report: NonNullable<Job["result"]>): HTMLElement {
  const section = document.createElement("section");
  section.className = "comparison-section";
  const heading = document.createElement("h3");
  heading.textContent = "PII redaction accuracy";
  section.append(heading);

  const entries = Object.entries(report.pii_accuracy ?? {});
  if (entries.length === 0) {
    const unavailable = document.createElement("p");
    unavailable.className = "row-meta";
    unavailable.textContent =
      "PII accuracy not scored. This report has no architecture-independent ground-truth annotations.";
    section.append(unavailable);
    return section;
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
  const table = document.createElement("table");
  table.innerHTML = `
    <thead><tr><th>Architecture</th><th>Precision</th><th>Recall</th><th>F1</th><th>Category accuracy</th><th>PII leakage</th><th>Alignment</th><th>TP / FP / FN</th></tr></thead>
    <tbody>${entries.map(([architectureId, metrics]) => {
      const label = report.architectures?.[architectureId]?.label ?? architectureId;
      return `<tr><td>${label}</td>
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
      </tr>`;
    }).join("")}</tbody>`;
  const note = document.createElement("p");
  note.className = "row-meta";
  note.textContent =
    "Exact source-turn spans determine precision, recall, F1, and leakage. Category accuracy is measured on matched spans; alignment excludes reference entities lost or changed by STT.";
  section.append(table, note);
  return section;
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
        <div class="row-title">${upload.label ?? upload.id}</div>
        <div class="row-meta">${describeUpload(upload) || "empty upload"}</div>
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
  report: NonNullable<Job["result"]>,
): HTMLElement {
  const wrapper = document.createElement("div");
  const entries = Object.entries(report.engines).sort(
    ([left], [right]) => ENGINE_ORDER.indexOf(left) - ENGINE_ORDER.indexOf(right),
  );
  wrapper.append(renderPricing(report));
  const participantWer = renderParticipantWer(entries);
  if (participantWer) wrapper.append(participantWer);
  wrapper.append(
    renderArchitectureResults(report),
    renderSttComparison(report, entries),
    renderPiiAccuracy(report),
  );

  for (const [, entry] of entries) {
    if (!entry.transcript && !entry.conversation) continue;
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
    wrapper.append(details);

    if (entry.transcript) {
      const flat = document.createElement("details");
      const flatSummary = document.createElement("summary");
      flatSummary.textContent = `Flat transcript — ${entry.label}`;
      const flatPre = document.createElement("pre");
      flatPre.textContent = entry.transcript;
      flat.append(flatSummary, flatPre);
      wrapper.append(flat);
    }
  }

  return wrapper;
}

export function renderCachedBenchmark(
  panel: HTMLElement,
  report: NonNullable<Job["result"]>,
): void {
  panel.replaceChildren(renderMetricsTable(report));
}

export function renderJobs(panel: HTMLElement, jobs: Job[], now = Date.now()): void {
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
