/**
 * Wiring. Every view reports on one selected recording — the built-in sample call
 * until the user runs their own — and the pricing controls own their own redraw so
 * the two-second job poll never rebuilds an input someone is typing into.
 */
import { api, type BenchmarkReport, type Job, type UploadMeta } from "./api.ts";
import { renderBusinessCase, renderPricingResults } from "./business.ts";
import {
  currentSettings,
  loadSettings,
  onSettingsChange,
  renderCalculator,
  syncMeasuredCallLength,
} from "./calculator.ts";
import { renderEvidence } from "./evidence.ts";
import {
  renderCachedBenchmark,
  renderCaveats,
  renderJobs,
  renderUploads,
} from "./render.ts";
import {
  BUILTIN_SOURCE_ID,
  collectSources,
  renderSourceBar,
  renderSourceNotice,
  type CallSource,
} from "./source.ts";
import { setupArchitectureTabs, setupViewTabs } from "./tabs.ts";

const POLL_INTERVAL_MS = 2000;

const el = <T extends HTMLElement>(id: string): T => {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing element #${id}`);
  return node as T;
};

const uploadForm = el<HTMLFormElement>("upload-form");
const audioInput = el<HTMLInputElement>("audio-input");
const transcriptInput = el<HTMLInputElement>("transcript-input");
const piiGroundTruthInput = el<HTMLInputElement>("pii-ground-truth-input");
const channel0Participant = el<HTMLInputElement>("channel-0-participant");
const channel1Participant = el<HTMLInputElement>("channel-1-participant");
const uploadButton = el<HTMLButtonElement>("upload-button");
const runDefaultButton = el<HTMLButtonElement>("run-default-button");
const uploadMessage = el<HTMLElement>("upload-message");
const backendStatus = el<HTMLElement>("backend-status");
const uploadsPanel = el<HTMLElement>("uploads");
const jobsPanel = el<HTMLElement>("jobs");
const cachedPanel = el<HTMLElement>("cached-results");
const businessSummary = el<HTMLElement>("business-summary");
const pricingControls = el<HTMLElement>("pricing-controls");
const pricingResults = el<HTMLElement>("pricing-results");
const evidencePanel = el<HTMLElement>("evidence");
const sourceBar = el<HTMLElement>("source-bar");
const sourceNotice = el<HTMLElement>("source-notice");
const runPanel = el<HTMLDetailsElement>("run-panel");

/** The built-in comparison, and whatever the user has since run. */
let builtinReport: BenchmarkReport | null = null;
let sources: CallSource[] = [];
let selectedSourceId = BUILTIN_SOURCE_ID;
let calculatorBuilt = false;
/** Jobs already seen as finished, so a completing run is auto-selected exactly once. */
const settledJobs = new Set<string>();

/* -------------------------------------------------------------------- tabs */

/** `#evidence` / `#technical` select a view, so a link can point at one. */
const VIEW_HASHES: Record<string, string> = {
  "#business": "view-business",
  "#evidence": "view-evidence",
  "#technical": "view-technical",
};

const views = setupViewTabs(
  el<HTMLElement>("view-tabs"),
  [
    el<HTMLElement>("view-business"),
    el<HTMLElement>("view-evidence"),
    el<HTMLElement>("view-technical"),
  ],
  (panelId) => {
    const hash = Object.entries(VIEW_HASHES).find(([, id]) => id === panelId)?.[0];
    if (hash && location.hash !== hash) history.replaceState(null, "", hash);
  },
);

function activateFromHash(): void {
  const panelId = VIEW_HASHES[location.hash];
  if (panelId) views.activate(panelId);
}

activateFromHash();
window.addEventListener("hashchange", activateFromHash);

setupArchitectureTabs(
  el<HTMLElement>("architecture-tabs"),
  el<HTMLIFrameElement>("architecture-diagram-frame"),
);

loadSettings();
el<HTMLElement>("caveats").replaceChildren(renderCaveats());

/* ------------------------------------------------------------------ render */

function selectedSource(): CallSource | undefined {
  return sources.find((source) => source.id === selectedSourceId) ?? sources[0];
}

function focusRunPanel(): void {
  views.activate("view-business");
  runPanel.open = true;
  runPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  audioInput.focus({ preventScroll: true });
}

/** Redraw everything that depends on which recording is selected. */
function redraw(): void {
  const source = selectedSource();
  if (!source) return;
  const report = source.report;

  // Build the pricing controls once the measured call length is known, so the
  // average-call-length default is that call rather than a round number.
  const settings = syncMeasuredCallLength(pricingControls, report.audio_seconds);
  if (!calculatorBuilt) {
    renderCalculator(pricingControls);
    calculatorBuilt = true;
  }

  renderSourceNotice(sourceNotice, source, focusRunPanel);
  renderBusinessCase(businessSummary, report, settings);
  renderPricingResults(pricingResults, report, settings);
  renderCachedBenchmark(cachedPanel, report, settings);
  renderEvidence(evidencePanel, source);
}

onSettingsChange(redraw);

function drawSourceBar(): void {
  renderSourceBar(sourceBar, sources, selectedSourceId, {
    onSelect: (sourceId) => {
      selectedSourceId = sourceId;
      redraw();
      drawSourceBar();
    },
    onUploadRequest: focusRunPanel,
  });
}

function refreshSources(jobs: Job[], uploads: UploadMeta[]): void {
  sources = collectSources(builtinReport, jobs, uploads);
  if (!sources.some((source) => source.id === selectedSourceId)) {
    selectedSourceId = sources[0]?.id ?? BUILTIN_SOURCE_ID;
  }
  drawSourceBar();
}

/* ------------------------------------------------------------------- state */

function setMessage(text: string, kind: "" | "error" | "success" = ""): void {
  uploadMessage.textContent = text;
  uploadMessage.className = `message ${kind}`.trim();
}

let latestJobs: Job[] = [];
let latestUploads: UploadMeta[] = [];

async function refresh(): Promise<void> {
  try {
    const [uploads, jobs] = await Promise.all([api.listUploads(), api.listJobs()]);
    latestUploads = uploads;
    latestJobs = jobs;
    renderUploads(uploadsPanel, uploads, {
      onDelete: (id, button) => void deleteUpload(id, button),
    });
    renderJobs(jobsPanel, jobs, Date.now(), currentSettings());

    // A run that has just finished becomes the selected recording: whoever started
    // it is waiting to see its numbers, not the sample's.
    const justFinished = jobs.find(
      (job) => job.status === "succeeded" && job.result && !settledJobs.has(job.id),
    );
    for (const job of jobs) {
      if (job.status === "succeeded" || job.status === "failed") settledJobs.add(job.id);
    }

    if (justFinished) selectedSourceId = justFinished.id;
    refreshSources(jobs, uploads);
    if (justFinished) {
      setMessage("Run finished. Every figure now reflects that call.", "success");
      redraw();
    }

    backendStatus.textContent = "backend online";
    backendStatus.className = "pill online";
  } catch (error) {
    backendStatus.textContent = `backend unreachable — ${(error as Error).message}`;
    backendStatus.className = "pill offline";
  }
}

async function deleteUpload(uploadId: string, button: HTMLButtonElement) {
  button.disabled = true;
  try {
    await api.deleteUpload(uploadId);
  } catch (error) {
    setMessage((error as Error).message, "error");
  } finally {
    await refresh();
  }
}

async function startRun(useAttachments: boolean): Promise<void> {
  const audio = useAttachments ? audioInput.files?.[0] : undefined;
  const transcript = useAttachments ? transcriptInput.files?.[0] : undefined;
  const piiGroundTruth = useAttachments ? piiGroundTruthInput.files?.[0] : undefined;

  if (useAttachments && !audio) {
    setMessage(
      "Attach a call recording first, or use “Re-run the built-in call”.",
      "error",
    );
    audioInput.focus();
    return;
  }

  uploadButton.disabled = true;
  runDefaultButton.disabled = true;
  setMessage(audio ? "Uploading audio and starting the run…" : "Starting the run…");
  try {
    if (audio || transcript) {
      const upload = await api.createUpload({
        audio,
        transcript,
        piiGroundTruth,
        channel0Participant: channel0Participant.value,
        channel1Participant: channel1Participant.value,
      });
      await api.startBenchmark(upload.id);
    } else {
      await api.startDefaultBenchmark();
    }
    uploadForm.reset();
    channel0Participant.value = "REP";
    channel1Participant.value = "CUSTOMER";
    setMessage(
      "Run started. It streams at 1x, so expect it to take about as long as the recording.",
      "success",
    );
  } catch (error) {
    setMessage((error as Error).message, "error");
  } finally {
    uploadButton.disabled = false;
    runDefaultButton.disabled = false;
    await refresh();
  }
}

uploadForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void startRun(true);
});
runDefaultButton.addEventListener("click", () => void startRun(false));

async function loadBuiltinBenchmark(): Promise<void> {
  try {
    builtinReport = await api.getDefaultBenchmark();
    refreshSources(latestJobs, latestUploads);
    redraw();
  } catch (error) {
    const message = `Saved comparison unavailable: ${(error as Error).message}`;
    for (const panel of [businessSummary, cachedPanel, evidencePanel]) {
      panel.textContent = message;
    }
    if (!calculatorBuilt) {
      renderCalculator(pricingControls);
      calculatorBuilt = true;
    }
  }
}

void Promise.all([loadBuiltinBenchmark(), refresh()]);
setInterval(() => void refresh(), POLL_INTERVAL_MS);
