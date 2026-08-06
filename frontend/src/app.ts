/** UI for uploading call audio/transcripts and running the benchmark suite. */
import { api } from "./api.ts";
import { renderCachedBenchmark, renderJobs, renderUploads } from "./render.ts";

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
const uploadMessage = el<HTMLElement>("upload-message");
const backendStatus = el<HTMLElement>("backend-status");
const uploadsPanel = el<HTMLElement>("uploads");
const jobsPanel = el<HTMLElement>("jobs");
const cachedPanel = el<HTMLElement>("cached-results");
const defaultTranscriptDetails = el<HTMLDetailsElement>("default-transcript");
const defaultTranscriptText = el<HTMLElement>("default-transcript-text");
let defaultTranscriptLoaded = false;

function setMessage(text: string, kind: "" | "error" | "success" = ""): void {
  uploadMessage.textContent = text;
  uploadMessage.className = `message ${kind}`.trim();
}

async function refresh(): Promise<void> {
  try {
    const [uploads, jobs] = await Promise.all([api.listUploads(), api.listJobs()]);
    renderUploads(uploadsPanel, uploads, {
      onDelete: (id, button) => void deleteUpload(id, button),
    });
    renderJobs(jobsPanel, jobs);
    backendStatus.textContent = "backend online";
    backendStatus.className = "status-pill online";
  } catch (error) {
    backendStatus.textContent = `backend unreachable — ${(error as Error).message}`;
    backendStatus.className = "status-pill offline";
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

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const audio = audioInput.files?.[0];
  const transcript = transcriptInput.files?.[0];
  const piiGroundTruth = piiGroundTruthInput.files?.[0];

  uploadButton.disabled = true;
  setMessage(audio ? "Uploading audio and starting benchmark…" : "Starting benchmark…");
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
    setMessage("Benchmark started. Results will update below.", "success");
  } catch (error) {
    setMessage((error as Error).message, "error");
  } finally {
    uploadButton.disabled = false;
    await refresh();
  }
});

async function loadCachedBenchmark(): Promise<void> {
  try {
    renderCachedBenchmark(cachedPanel, await api.getDefaultBenchmark());
  } catch (error) {
    cachedPanel.textContent = `Cached comparison unavailable: ${(error as Error).message}`;
  }
}

defaultTranscriptDetails.addEventListener("toggle", async () => {
  if (!defaultTranscriptDetails.open || defaultTranscriptLoaded) return;
  defaultTranscriptLoaded = true;
  try {
    defaultTranscriptText.textContent = await api.getDefaultTranscript();
  } catch (error) {
    defaultTranscriptLoaded = false;
    defaultTranscriptText.textContent =
      `Default transcript unavailable: ${(error as Error).message}`;
  }
});

void Promise.all([loadCachedBenchmark(), refresh()]);
setInterval(() => void refresh(), POLL_INTERVAL_MS);
