/** UI for uploading call audio/transcripts and running the benchmark suite. */
import { api } from "./api.ts";
import { renderJobs, renderUploads } from "./render.ts";

const POLL_INTERVAL_MS = 2000;

const el = <T extends HTMLElement>(id: string): T => {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing element #${id}`);
  return node as T;
};

const uploadForm = el<HTMLFormElement>("upload-form");
const audioInput = el<HTMLInputElement>("audio-input");
const transcriptInput = el<HTMLInputElement>("transcript-input");
const uploadButton = el<HTMLButtonElement>("upload-button");
const uploadMessage = el<HTMLElement>("upload-message");
const backendStatus = el<HTMLElement>("backend-status");
const uploadsPanel = el<HTMLElement>("uploads");
const jobsPanel = el<HTMLElement>("jobs");

function setMessage(text: string, kind: "" | "error" | "success" = ""): void {
  uploadMessage.textContent = text;
  uploadMessage.className = `message ${kind}`.trim();
}

async function refresh(): Promise<void> {
  try {
    const [uploads, jobs] = await Promise.all([api.listUploads(), api.listJobs()]);
    renderUploads(uploadsPanel, uploads, {
      onBenchmark: (id, button) => void startBenchmark(id, button),
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

async function startBenchmark(uploadId: string, button: HTMLButtonElement) {
  button.disabled = true;
  try {
    await api.startBenchmark(uploadId);
    setMessage("Benchmark started.", "success");
  } catch (error) {
    setMessage((error as Error).message, "error");
  } finally {
    button.disabled = false;
    await refresh();
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

  if (!audio && !transcript) {
    setMessage("Choose an audio file, a transcript, or both.", "error");
    return;
  }

  uploadButton.disabled = true;
  setMessage(audio ? "Uploading and decoding audio…" : "Uploading…");
  try {
    await api.createUpload({ audio, transcript });
    uploadForm.reset();
    setMessage("Uploaded.", "success");
  } catch (error) {
    setMessage((error as Error).message, "error");
  } finally {
    uploadButton.disabled = false;
    await refresh();
  }
});

void refresh();
setInterval(() => void refresh(), POLL_INTERVAL_MS);
