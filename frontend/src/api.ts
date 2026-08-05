/** Typed client for the benchmark API. */

export interface AudioMeta {
  filename: string | null;
  duration_seconds: number;
  sample_rate: number;
  transcoded: boolean;
  size_bytes: number;
}

export interface TranscriptMeta {
  filename: string | null;
  characters: number;
  lines: number;
}

export interface UploadMeta {
  id: string;
  created_at: string;
  audio: AudioMeta | null;
  transcript: TranscriptMeta | null;
}

export interface LagStats {
  mean: number | null;
  median: number | null;
  p95: number | null;
  max: number | null;
  count: number;
}

export interface EngineMetrics {
  mode: string;
  wall_seconds: number;
  turnaround_seconds?: number;
  /** Seconds from the start of the call until the full transcript exists. */
  time_to_full_transcript: number;
  finalization_lag: LagStats;
  segments: number;
  word_count: number;
  wer?: number;
  accuracy?: number;
  substitutions?: number;
  deletions?: number;
  insertions?: number;
  hits?: number;
}

export interface EngineResult {
  label: string;
  metrics?: EngineMetrics;
  transcript?: string;
  error?: string;
}

export interface BenchmarkReport {
  audio_seconds: number;
  vad_utterances: number;
  reference_words: number | null;
  scored: boolean;
  engines: Record<string, EngineResult>;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed";
export type EngineState = "pending" | "running" | "done" | "failed";

export interface Job {
  id: string;
  upload_id: string;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  scored: boolean;
  engines: Record<string, EngineState>;
  engine_labels: Record<string, string>;
  result: BenchmarkReport | null;
  error: string | null;
  traceback?: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body; the status line is the best we have */
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),

  listUploads: () => request<UploadMeta[]>("/api/uploads"),

  createUpload(files: { audio?: File; transcript?: File }): Promise<UploadMeta> {
    const form = new FormData();
    if (files.audio) form.append("audio", files.audio);
    if (files.transcript) form.append("transcript", files.transcript);
    return request<UploadMeta>("/api/uploads", { method: "POST", body: form });
  },

  deleteUpload: (id: string) =>
    request<void>(`/api/uploads/${id}`, { method: "DELETE" }),

  startBenchmark: (id: string) =>
    request<Job>(`/api/uploads/${id}/benchmark`, { method: "POST" }),

  listJobs: () => request<Job[]>("/api/jobs"),

  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),
};
