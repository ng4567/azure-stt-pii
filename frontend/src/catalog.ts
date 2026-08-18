/**
 * The two architectures, and where their artifacts live in the repository.
 *
 * A cached benchmark report may still carry engines and architectures that were
 * retired (architecture 3 lived in the checked-in stereo result). Everything the
 * UI renders is filtered through the allow-lists here, so a retired key is
 * ignored rather than sorted to the front by an `indexOf` of -1.
 */

export const ARCH_LEGACY = "architecture-1-azure-language";
export const ARCH_MODERN = "architecture-2-mai-realtime-deepseek";

export const ENGINE_LEGACY = "architecture-1-azure-speech-realtime";
export const ENGINE_MODERN = "architecture-2-mai-transcribe-realtime";

/** Render order. Membership doubles as the allow-list. */
export const ARCHITECTURE_ORDER = [ARCH_LEGACY, ARCH_MODERN] as const;
export const ENGINE_ORDER = [ENGINE_LEGACY, ENGINE_MODERN] as const;

/**
 * Canonical id for a report key, or null if it names nothing we render.
 *
 * Some report sections key on a shortened id ("architecture-1") rather than the
 * full pipeline id, so an unambiguous prefix resolves too.
 */
export function resolveArchitecture(key: string): string | null {
  if ((ARCHITECTURE_ORDER as readonly string[]).includes(key)) return key;
  const matches = ARCHITECTURE_ORDER.filter((id) => id.startsWith(`${key}-`));
  return matches.length === 1 ? matches[0]! : null;
}

export function isKnownArchitecture(id: string): boolean {
  return resolveArchitecture(id) !== null;
}

export function isKnownEngine(id: string): boolean {
  return (ENGINE_ORDER as readonly string[]).includes(id);
}

/** Order-and-filter in one pass, for `Object.entries(...)` of a report map. */
export function orderedEntries<T>(
  source: Record<string, T> | undefined,
  order: readonly string[],
): Array<[string, T]> {
  return Object.entries(source ?? {})
    .filter(([key]) => order.includes(key))
    .sort(([left], [right]) => order.indexOf(left) - order.indexOf(right));
}

export interface ArchitectureProfile {
  id: string;
  engineId: string;
  /** Seller-facing name. The backend label ("1. Azure Speech + …") is developer-facing. */
  name: string;
  kind: "legacy" | "modern";
  tagline: string;
  stack: string[];
  returns: string;
}

export const PROFILES: Record<string, ArchitectureProfile> = {
  [ARCH_LEGACY]: {
    id: ARCH_LEGACY,
    engineId: ENGINE_LEGACY,
    name: "Today — Azure Speech + Azure AI Language",
    kind: "legacy",
    tagline:
      "The first-party stack most contact centres are on now. Azure Speech transcribes " +
      "the call live, then Conversation PII and conversation summarization run against " +
      "the transcript.",
    stack: [
      "Azure Speech real-time transcription",
      "Azure AI Language — Conversation PII",
      "Azure AI Language — conversation summarization",
    ],
    returns: "Redacted transcript, typed entity list, and a summary.",
  },
  [ARCH_MODERN]: {
    id: ARCH_MODERN,
    engineId: ENGINE_MODERN,
    name: "Modernized — MAI-Transcribe + Foundry + Fabric",
    kind: "modern",
    tagline:
      "The same call, transcribed live by MAI-Transcribe-1.5 over Voice Live and " +
      "summarized by one strict-schema model call in Microsoft Foundry, landing " +
      "PII-safe in Fabric for analytics.",
    stack: [
      "MAI-Transcribe-1.5 real-time (Voice Live)",
      "DeepSeek-V4 Flash in Microsoft Foundry",
      "Oracle → Fabric Mirroring → OneLake → Data Agent",
    ],
    returns: "A PII-safe summary, plus governed analytics over every call.",
  },
};

export function profile(architectureId: string): ArchitectureProfile | undefined {
  return PROFILES[architectureId];
}

/* ---------------------------------------------------------------- repository */

const REPO = "https://github.com/ng4567/azure-stt-pii";
const BRANCH = "main";

/** A file in this repository, viewable on GitHub. */
export function repoFile(path: string): string {
  return `${REPO}/blob/${BRANCH}/${path}`;
}

export interface RepoAsset {
  path: string;
  label: string;
  description: string;
}

/** Per-architecture transcript produced by the checked-in stereo run. */
export const ENGINE_TRANSCRIPT_PATHS: Record<string, string> = {
  [ENGINE_LEGACY]:
    "data/mock-call-stereo-transcript-architecture-1-azure-speech-realtime.txt",
  [ENGINE_MODERN]:
    "data/mock-call-stereo-transcript-architecture-2-mai-transcribe-realtime.txt",
};

/** Per-architecture speaker-turn conversation, with offsets and channel identity. */
export const ENGINE_CONVERSATION_PATHS: Record<string, string> = {
  [ENGINE_LEGACY]:
    "data/mock-call-stereo-conversation-architecture-1-azure-speech-realtime.json",
  [ENGINE_MODERN]:
    "data/mock-call-stereo-conversation-architecture-2-mai-transcribe-realtime.json",
};

export const REFERENCE_TRANSCRIPT: RepoAsset = {
  path: "data/mock-call-transcript.txt",
  label: "Reference transcript",
  description:
    "The hand-written script. It is the text-to-speech input, the word-error-rate " +
    "reference, and the character-offset basis for the PII annotations.",
};

export const CALL_AUDIO: RepoAsset = {
  path: "data/mock-call-stereo.wav",
  label: "Call audio (stereo)",
  description:
    "8m 24s synthesized call, REP on channel 0 and CUSTOMER on channel 1, so speaker " +
    "identity comes from the channel rather than from billed diarization.",
};

export const SUPPORTING_ASSETS: RepoAsset[] = [
  {
    path: "data/mock-call-pii-ground-truth.json",
    label: "PII ground truth",
    description: "26 annotated PII spans, checksummed against the reference transcript.",
  },
  {
    path: "data/mock-call-stereo-turns.json",
    label: "Reference turn timings",
    description: "Per-turn offsets and durations for the stereo render.",
  },
  {
    path: "data/mock-call-stereo-stt-benchmark-results.json",
    label: "Benchmark result",
    description: "The saved run every number on this page is read from.",
  },
  {
    path: "data/system_prompt.txt",
    label: "Summarization system prompt",
    description: "Passed verbatim as the model system message in the modernized path.",
  },
  {
    path: "data/stt.py",
    label: "Benchmark harness",
    description: "The single implementation shared by the CLI and this app.",
  },
];
