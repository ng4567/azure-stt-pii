/**
 * Call evidence: the recording, the reference script, and what each stack made of
 * it — every artifact linked back to the file it came from in the repository, so a
 * customer can check any number on the other tabs against the source.
 */
import { api, type BenchmarkReport, type Conversation } from "./api.ts";
import {
  ARCHITECTURE_ORDER,
  CALL_AUDIO,
  ENGINE_CONVERSATION_PATHS,
  ENGINE_TRANSCRIPT_PATHS,
  PROFILES,
  REFERENCE_TRANSCRIPT,
  SUPPORTING_ASSETS,
  orderedEntries,
  ENGINE_ORDER,
  repoFile,
} from "./catalog.ts";
import { escapeHtml, formatPercent } from "./format.ts";
import type { CallSource } from "./source.ts";

function element(tag: string, className: string, html = ""): HTMLElement {
  const node = document.createElement(tag);
  node.className = className;
  if (html) node.innerHTML = html;
  return node;
}

function repoLink(path: string, label = "View on GitHub"): string {
  return `<a class="repo-link" href="${repoFile(path)}" target="_blank" rel="noreferrer">
    <span class="repo-link__path">${escapeHtml(path)}</span>
    <span class="repo-link__action">${escapeHtml(label)}</span>
  </a>`;
}

/** Turns rendered as `[12.34s] REP: …`, the same view the CLI writes. */
function conversationText(conversation: Conversation): string {
  return conversation.conversationItems
    .map((turn) => `[${(turn.offset / 10_000_000).toFixed(2)}s] ${turn.participantId}: ${turn.text}`)
    .join("\n");
}

/* --------------------------------------------------------------- the recording */

function renderSource(source: CallSource): HTMLElement {
  const section = element("section", "panel panel--evidence-source");
  section.append(
    element(
      "div",
      "panel__head",
      `<h2>The call</h2>
       <p class="panel__hint">${
         source.builtin
           ? "One synthesized customer-service call, shipped with this repository."
           : "Your own recording."
       } Both stacks receive exactly this audio, so every difference on the other tabs comes from the stack rather than the input.</p>`,
    ),
  );

  const grid = element("div", "evidence-grid");

  // Repository links only make sense for the call that lives in the repository.
  const audioSrc = source.builtin
    ? "/api/benchmark/default/audio"
    : `/api/uploads/${encodeURIComponent(source.uploadId ?? "")}/audio`;

  const audio = element(
    "article",
    "evidence-card",
    `<h3>${escapeHtml(source.builtin ? CALL_AUDIO.label : "Your call audio")}</h3>
     <p>${escapeHtml(
       source.builtin
         ? CALL_AUDIO.description
         : `${source.detail}. Normalized to 16-bit PCM before transcription; channel identity is preserved.`,
     )}</p>
     <audio controls preload="none" src="${audioSrc}">
       Your browser cannot play this recording.
     </audio>
     ${source.builtin ? repoLink(CALL_AUDIO.path, "Download from GitHub") : ""}`,
  );

  const scored = source.report.scored;
  const transcript = element(
    "article",
    "evidence-card evidence-card--transcript",
    `<h3>${escapeHtml(REFERENCE_TRANSCRIPT.label)}</h3>
     <p>${escapeHtml(
       source.builtin
         ? REFERENCE_TRANSCRIPT.description
         : scored
           ? "The reference you uploaded. Word error rate is scored against it."
           : "No reference transcript was uploaded with this call, so word error rate is not scored. Latency, cost, and both transcripts are still measured.",
     )}</p>
     ${
       scored
         ? `<div class="transcript-actions">
              <button type="button" class="ghost" id="reference-toggle" aria-expanded="false"
                      aria-controls="reference-text">Read the transcript</button>
            </div>
            <pre id="reference-text" class="transcript-text" hidden>Loading…</pre>`
         : ""
     }
     ${source.builtin ? repoLink(REFERENCE_TRANSCRIPT.path) : ""}`,
  );

  const toggle = transcript.querySelector<HTMLButtonElement>("#reference-toggle");
  const text = transcript.querySelector<HTMLPreElement>("#reference-text");
  let loaded = false;
  toggle?.addEventListener("click", async () => {
    if (!text) return;
    const opening = text.hidden;
    text.hidden = !opening;
    toggle.setAttribute("aria-expanded", String(opening));
    toggle.textContent = opening ? "Hide the transcript" : "Read the transcript";
    if (!opening || loaded) return;
    loaded = true;
    try {
      text.textContent = source.builtin
        ? await api.getDefaultTranscript()
        : await api.getUploadTranscript(source.uploadId ?? "");
    } catch (error) {
      loaded = false;
      text.textContent = `Reference transcript unavailable: ${(error as Error).message}`;
    }
  });

  grid.append(audio, transcript);
  section.append(grid);
  return section;
}

/* ------------------------------------------------------------ what each produced */

function renderTranscriptions(report: BenchmarkReport, builtin: boolean): HTMLElement {
  const section = element("section", "panel");
  const hint = report.scored
    ? "Scored against the reference above. Open either to read the speaker turns the pipeline actually produced."
    : "No reference transcript was provided, so word error rate is not scored. Open either to read the speaker turns the pipeline produced.";
  section.append(
    element(
      "div",
      "panel__head",
      `<h2>What each stack heard</h2>
       <p class="panel__hint">${hint}</p>`,
    ),
  );

  const engines = orderedEntries(report.engines, ENGINE_ORDER);
  if (engines.length === 0) {
    section.append(element("p", "empty", "The saved run has no transcripts."));
    return section;
  }

  const grid = element("div", "evidence-grid");
  for (const [engineId, entry] of engines) {
    const wer = entry.metrics?.wer;
    const card = element("article", "evidence-card");
    card.innerHTML = `
      <h3>${escapeHtml(entry.label)}</h3>
      <p class="evidence-card__stat">
        Word error rate <strong>${formatPercent(wer)}</strong>
        ${entry.metrics ? ` · ${entry.metrics.segments} speaker turns` : ""}
      </p>
      <details>
        <summary>Speaker turns</summary>
        <pre class="transcript-text"></pre>
      </details>
      ${builtin && ENGINE_TRANSCRIPT_PATHS[engineId] ? repoLink(ENGINE_TRANSCRIPT_PATHS[engineId]!, "Flat transcript on GitHub") : ""}
      ${builtin && ENGINE_CONVERSATION_PATHS[engineId] ? repoLink(ENGINE_CONVERSATION_PATHS[engineId]!, "Speaker turns on GitHub") : ""}`;

    const pre = card.querySelector("pre");
    if (pre) {
      pre.textContent = entry.conversation
        ? conversationText(entry.conversation)
        : entry.transcript ?? "No transcript in this run.";
    }
    grid.append(card);
  }

  section.append(grid);
  return section;
}

function renderOutputs(report: BenchmarkReport): HTMLElement {
  const section = element("section", "panel");
  section.append(
    element(
      "div",
      "panel__head",
      `<h2>What each stack returned</h2>
       <p class="panel__hint">The PII-safe artifacts a downstream system would actually store.</p>`,
    ),
  );

  const architectures = orderedEntries(report.architectures, ARCHITECTURE_ORDER);
  if (architectures.length === 0) {
    section.append(
      element("p", "empty", "This saved run predates end-to-end pipeline outputs."),
    );
    return section;
  }

  const grid = element("div", "evidence-grid");
  for (const [architectureId, result] of architectures) {
    const info = PROFILES[architectureId];
    const card = element("article", "evidence-card");

    if (result.status === "failed") {
      card.innerHTML = `<h3>${escapeHtml(info?.name ?? result.label)}</h3>
        <p class="message error">${escapeHtml(result.error ?? "Architecture failed.")}</p>`;
      grid.append(card);
      continue;
    }

    card.innerHTML = `
      <h3>${escapeHtml(info?.name ?? result.label)}</h3>
      <h4>Summary</h4>
      <pre class="transcript-text" data-slot="summary"></pre>
      ${
        result.redacted
          ? `<details>
               <summary>Redacted transcript · ${result.entities.length} typed entities</summary>
               <pre class="transcript-text" data-slot="redacted"></pre>
             </details>`
          : `<p class="section-note">Returns a PII-safe summary only — no redacted transcript
              and no entity list, because nothing unredacted is retained to redact.</p>`
      }`;

    const summary = card.querySelector<HTMLElement>('[data-slot="summary"]');
    if (summary) summary.textContent = result.summary ?? "No summary in this run.";
    const redacted = card.querySelector<HTMLElement>('[data-slot="redacted"]');
    if (redacted) redacted.textContent = result.redacted?.transcript ?? "";

    grid.append(card);
  }

  section.append(grid);
  return section;
}

function renderSupporting(): HTMLElement {
  const section = element("section", "panel");
  section.append(
    element(
      "div",
      "panel__head",
      `<h2>Everything else, in the repository</h2>
       <p class="panel__hint">The fixtures, annotations, and harness behind every figure on this site.</p>`,
    ),
  );
  const list = element("ul", "asset-list");
  list.innerHTML = SUPPORTING_ASSETS.map(
    (asset) => `
    <li>
      <span class="asset-list__label">${escapeHtml(asset.label)}</span>
      <span class="asset-list__description">${escapeHtml(asset.description)}</span>
      ${repoLink(asset.path)}
    </li>`,
  ).join("");
  section.append(list);
  return section;
}

export function renderEvidence(host: HTMLElement, source: CallSource): void {
  const children: HTMLElement[] = [
    renderSource(source),
    renderTranscriptions(source.report, source.builtin),
    renderOutputs(source.report),
  ];
  // The repository fixtures describe the built-in call; they say nothing about
  // someone else's recording, so they are not shown alongside one.
  if (source.builtin) children.push(renderSupporting());
  host.replaceChildren(...children);
}
