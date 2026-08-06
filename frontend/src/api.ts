/** Typed client for the benchmark API. */

export interface AudioMeta {
  filename: string | null;
  duration_seconds: number;
  sample_rate: number;
  channels: number;
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
  /** True for the checked-in mock call, which is seeded and cannot be deleted. */
  builtin?: boolean;
  label?: string | null;
  channel_map?: Record<string, string>;
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
  channel_sessions?: number;
  utterance_requests?: number;
  timing_estimated?: boolean;
  word_count: number;
  wer?: number;
  accuracy?: number;
  substitutions?: number;
  deletions?: number;
  insertions?: number;
  hits?: number;
  participants?: Record<string, {
    wer: number;
    accuracy: number;
    substitutions: number;
    deletions: number;
    insertions: number;
    hits: number;
  }>;
}

export interface AudioTiming {
  word: string;
  offset: number;
  duration: number;
}

export interface ConversationTurn {
  id: string;
  participantId: string;
  channel: number;
  offset: number;
  duration: number;
  text: string;
  lexical?: string;
  itn?: string;
  maskedItn?: string;
  audioTimings?: AudioTiming[];
}

export interface Conversation {
  id: string;
  language: string;
  modality: "transcript";
  speakerAttributed: boolean;
  channelMap: Record<string, string>;
  conversationItems: ConversationTurn[];
}

export interface EngineResult {
  label: string;
  metrics?: EngineMetrics;
  transcript?: string;
  conversation?: Conversation | null;
  error?: string;
}

export type ArchitectureStatus = "succeeded" | "failed";
export type ArchitectureState = "pending" | "running" | "done" | "failed";

export interface ArchitectureStage {
  status: "succeeded" | "failed" | "skipped";
  provider: string;
  model: string;
  wall_seconds: number;
  metrics: Record<string, unknown>;
  error: string | null;
}

export interface PiiEntity {
  category: string;
  text: string;
  turn_id: string;
  offset: number;
  length: number;
  confidence: number | null;
  placeholder: string;
}

export interface ArchitectureResult {
  schema_version: "1.0";
  architecture_id: string;
  label: string;
  status: ArchitectureStatus;
  source: { transcript: string; conversation: Conversation } | null;
  redacted: { transcript: string; conversation: Conversation } | null;
  summary: string | null;
  entities: PiiEntity[];
  stages: Record<string, ArchitectureStage>;
  error: string | null;
}

export interface BenchmarkReport {
  audio_seconds: number;
  channel_count: number;
  channel_map: Record<string, string>;
  speaker_attributed: boolean;
  vad_utterances: number;
  reference_words: number | null;
  scored: boolean;
  engines: Record<string, EngineResult>;
  /** Absent from historical cached reports created before downstream pipelines. */
  architectures?: Record<string, ArchitectureResult>;
  /** Measured usage retained when cached reports omit full downstream results. */
  pricing_usage?: Record<string, Record<string, number>>;
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
  architectures: Record<string, ArchitectureState>;
  architecture_labels: Record<string, string>;
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

  createUpload(files: {
    audio?: File;
    transcript?: File;
    channel0Participant?: string;
    channel1Participant?: string;
  }): Promise<UploadMeta> {
    const form = new FormData();
    if (files.audio) form.append("audio", files.audio);
    if (files.transcript) form.append("transcript", files.transcript);
    form.append("channel_0_participant", files.channel0Participant ?? "REP");
    form.append("channel_1_participant", files.channel1Participant ?? "CUSTOMER");
    return request<UploadMeta>("/api/uploads", { method: "POST", body: form });
  },

  deleteUpload: (id: string) =>
    request<void>(`/api/uploads/${id}`, { method: "DELETE" }),

  getDefaultBenchmark: () =>
    request<BenchmarkReport>("/api/benchmark/default"),

  startDefaultBenchmark: () =>
    request<Job>("/api/benchmark", { method: "POST" }),

  startBenchmark: (id: string) =>
    request<Job>(`/api/uploads/${id}/benchmark`, { method: "POST" }),

  listJobs: () => request<Job[]>("/api/jobs"),

  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),

  getArchitectureResult: (jobId: string, architectureId: string) =>
    request<ArchitectureResult>(`/api/jobs/${jobId}/architectures/${architectureId}`),
};
