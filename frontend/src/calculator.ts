/**
 * The seller's controls: three contract discounts plus call volume.
 *
 * These live outside the two-second job poll on purpose — that loop replaces the
 * DOM it owns, and an input rebuilt mid-keystroke loses focus and value. The panel
 * is built once, and changes are broadcast to whatever wants to redraw.
 */
import { DEFAULT_SETTINGS, type PricingSettings } from "./pricing.ts";

const STORAGE_KEY = "azure-stt-pii.pricing-settings.v1";

let settings: PricingSettings = { ...DEFAULT_SETTINGS };
/** True once the seller has set something themselves, so defaults stop overriding. */
let userSupplied = false;
/** The measured call length, which "reset" returns to rather than a round number. */
let measuredCallMinutes = DEFAULT_SETTINGS.averageCallMinutes;
const listeners = new Set<(next: PricingSettings) => void>();

/**
 * Minutes as the input shows them: at most two decimals, no trailing zeros. The
 * measured length is kept at full precision underneath, so the projection still
 * reproduces the measurement exactly; only the digits a seller sees are trimmed —
 * "8.4", not "8.402800000000001".
 */
function displayMinutes(value: number): string {
  return String(Number(value.toFixed(2)));
}

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

function sanitize(raw: Partial<PricingSettings>): PricingSettings {
  return {
    speechDiscount: clamp(Number(raw.speechDiscount ?? 0), 0, 0.99),
    azureLanguageDiscount: clamp(Number(raw.azureLanguageDiscount ?? 0), 0, 0.99),
    foundryLlmDiscount: clamp(Number(raw.foundryLlmDiscount ?? 0), 0, 0.99),
    monthlyCalls: Math.round(clamp(Number(raw.monthlyCalls ?? 0), 0, 100_000_000)),
    averageCallMinutes: clamp(Number(raw.averageCallMinutes ?? 0), 0.1, 600),
  };
}

export function loadSettings(): PricingSettings {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      settings = sanitize(JSON.parse(stored) as Partial<PricingSettings>);
      userSupplied = true;
    }
  } catch {
    /* private mode, or a settings blob from an older build; defaults are fine */
  }
  return settings;
}

/**
 * Default the average call length to the call that was actually measured.
 *
 * A hardcoded round number would silently rescale every figure: 8.4 minutes is
 * 0.17s short of the 504.168s benchmark call, which is enough to drag the
 * business case 0.03% below the per-call cost the technical view reports for the
 * very same run. Starting at the measured length makes the projection reproduce
 * the measurement exactly, so any deviation the seller sees is their own input.
 */
export function applyMeasuredCallLength(audioSeconds: number): PricingSettings {
  if (!Number.isFinite(audioSeconds) || audioSeconds <= 0) return settings;
  measuredCallMinutes = audioSeconds / 60;
  if (userSupplied) return settings;
  settings = sanitize({ ...settings, averageCallMinutes: measuredCallMinutes });
  return settings;
}

/**
 * Apply a selected recording's measured length and keep an existing calculator
 * control in sync. This is separate from rendering so source changes do not
 * rebuild the form or overwrite a seller's own input.
 */
export function syncMeasuredCallLength(
  host: HTMLElement,
  audioSeconds: number,
): PricingSettings {
  const previousCallMinutes = settings.averageCallMinutes;
  const next = applyMeasuredCallLength(audioSeconds);
  if (next.averageCallMinutes !== previousCallMinutes) {
    const input = host.querySelector<HTMLInputElement>("#volume-call-minutes");
    if (input) input.value = displayMinutes(next.averageCallMinutes);
  }
  return next;
}

export function currentSettings(): PricingSettings {
  return settings;
}

export function onSettingsChange(listener: (next: PricingSettings) => void): void {
  listeners.add(listener);
}

function update(patch: Partial<PricingSettings>): void {
  settings = sanitize({ ...settings, ...patch });
  userSupplied = true;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    /* persistence is a convenience, not a requirement */
  }
  for (const listener of listeners) listener(settings);
}

interface DiscountField {
  key: "speechDiscount" | "azureLanguageDiscount" | "foundryLlmDiscount";
  label: string;
  applies: string;
  hint?: string;
}

const DISCOUNT_FIELDS: DiscountField[] = [
  {
    key: "speechDiscount",
    label: "Azure AI Speech",
    applies: "Azure Speech STT and MAI-Transcribe-1.5",
    hint: "Both paths bill against the same Speech contract line, so this discount applies to each architecture's transcription.",
  },
  {
    key: "azureLanguageDiscount",
    label: "Azure AI Language",
    applies: "Conversation PII and conversation summarization",
    hint: "Only the current-state architecture consumes Azure AI Language.",
  },
  {
    key: "foundryLlmDiscount",
    label: "Foundry model",
    applies: "Summarization input and output tokens",
    hint: "Use this to account for a provisioned-throughput (PTU) deployment. PTU is billed as reserved hourly capacity rather than per token, so an effective discount at your expected utilization is the closest per-call equivalent.",
  },
];

function numberField(
  id: string,
  label: string,
  value: string,
  attrs: Record<string, string>,
  hint: string,
): string {
  const attributes = Object.entries(attrs)
    .map(([name, entry]) => `${name}="${entry}"`)
    .join(" ");
  return `
    <label class="knob" for="${id}">
      <span class="knob__label">${label}</span>
      <span class="knob__input">
        <input type="number" id="${id}" value="${value}" ${attributes} />
      </span>
      <span class="knob__hint">${hint}</span>
    </label>`;
}

/** Build the control panel once and wire it to `update`. */
export function renderCalculator(host: HTMLElement): void {
  const percent = (value: number): string => String(Math.round(value * 100));

  host.innerHTML = `
    <div class="knobs knobs--discounts">
      ${DISCOUNT_FIELDS.map((field) => `
        <label class="knob knob--discount" for="discount-${field.key}">
          <span class="knob__label">${field.label} discount</span>
          <span class="knob__input knob__input--suffix" data-suffix="%">
            <input type="number" id="discount-${field.key}" min="0" max="99" step="1"
                   value="${percent(settings[field.key])}" />
          </span>
          <span class="knob__applies">${field.applies}</span>
          ${field.hint ? `<span class="knob__hint">${field.hint}</span>` : ""}
        </label>`).join("")}
    </div>
    <div class="knobs knobs--volume">
      ${numberField(
        "volume-monthly-calls",
        "Calls per month",
        String(settings.monthlyCalls),
        { min: "0", max: "100000000", step: "1000" },
        "Drives the monthly and annual projection, and which Conversation PII volume tier the current-state cost lands in.",
      )}
      ${numberField(
        "volume-call-minutes",
        "Average call length",
        displayMinutes(settings.averageCallMinutes),
        { min: "0.1", max: "600", step: "any" },
        "Minutes. Defaults to the length of the call that was measured. Usage scales linearly from there — audio hours, transcript characters, and prompt tokens all grow with call length.",
      )}
    </div>
    <p class="knobs__reset">
      <button type="button" class="ghost" id="pricing-reset">Reset to list price</button>
    </p>`;

  for (const field of DISCOUNT_FIELDS) {
    const input = host.querySelector<HTMLInputElement>(`#discount-${field.key}`);
    input?.addEventListener("input", () => {
      update({ [field.key]: Number(input.value) / 100 } as Partial<PricingSettings>);
    });
  }

  const calls = host.querySelector<HTMLInputElement>("#volume-monthly-calls");
  calls?.addEventListener("input", () => update({ monthlyCalls: Number(calls.value) }));

  const minutes = host.querySelector<HTMLInputElement>("#volume-call-minutes");
  minutes?.addEventListener("input", () =>
    update({ averageCallMinutes: Number(minutes.value) }),
  );

  host.querySelector<HTMLButtonElement>("#pricing-reset")?.addEventListener("click", () => {
    // Back to list price and the measured call, not to a hardcoded round number.
    const reset = { ...DEFAULT_SETTINGS, averageCallMinutes: measuredCallMinutes };
    update(reset);
    for (const field of DISCOUNT_FIELDS) {
      const control = host.querySelector<HTMLInputElement>(`#discount-${field.key}`);
      if (control) control.value = percent(reset[field.key]);
    }
    if (calls) calls.value = String(reset.monthlyCalls);
    if (minutes) minutes.value = displayMinutes(reset.averageCallMinutes);
  });
}
