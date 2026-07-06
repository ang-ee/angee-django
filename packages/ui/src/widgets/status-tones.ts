import { stateToneFromValue, type Tone, type ToneValueBuckets } from "../lib/tones";

/**
 * The shared status vocabulary: which status-string values render in which tone. One
 * owner for "what color is this status", consumed by every status display — the
 * `statusBadge` pill, the `colorDot` dot, and the operator console's `StateTag` — so
 * they cannot drift (each previously kept its own divergent private map).
 *
 * It lives at the widget layer, never in `lib/tones.ts` (which stays domain-free): the
 * color *mechanism* is a framework fact, the status *vocabulary* a product one. A caller
 * overrides any value with an explicit `<Column tone={{ VALUE: "tone" }}>` map (which
 * wins); spread `STATUS_TONES` to extend rather than replace it. An unmapped value falls
 * to `brand` — the deliberate "unknown status" tone.
 *
 * Tones read as: success = live/healthy/done · warning = in-flight/needs-attention ·
 * danger = failed/hard-down · neutral = dormant/inert. The run-state axis the colored
 * dot shows maps stopped→neutral (grey), running→success (green), error→danger (red),
 * warning→warning (amber); see `docs/frontend/guidelines.md`.
 */
export const STATUS_TONES: ToneValueBuckets = {
  success: [
    "active", "published", "approved", "live", "open", "done",
    "running", "ready", "up", "online", "healthy", "completed",
    "succeeded",
    // Document lifecycle (accounting/sales): a posted/paid/confirmed/invoiced
    // document has reached its healthy terminal state.
    "posted", "paid", "confirmed", "invoiced",
  ],
  warning: [
    "draft", "paused", "review", "pending", "in_review",
    "provisioning", "deprovisioning", "starting", "connecting",
    "closed", "warning", "degraded", "waiting",
    // Document lifecycle: awaiting money or an invoice — in-flight, needs attention.
    "not_paid", "partial", "to_invoice",
  ],
  danger: ["error", "failed", "denied", "lost", "down", "crashed"],
  info: ["started", "assigned"],
  neutral: [
    "archived", "deleted", "disabled", "rejected", "blocked",
    "stopped", "deprovisioned", "idle", "inactive", "offline", "unknown", "default",
    "scheduled", "canceled", "skipped",
    // Document lifecycle: cancelled (British spelling used by the ledger enums),
    // and "nothing to invoice" — an inert, no-action state.
    "cancelled", "nothing",
  ],
};

export interface StatusToneOptions {
  /** Tone for a non-empty value absent from the shared vocabulary. */
  unknownTone?: Tone;
  /** Tone for null/undefined/empty values. */
  emptyTone?: Tone;
}

/**
 * Resolve a status value's tone. The caller's explicit `<Column tone>` entry wins —
 * keyed on the value exactly as it reads (the same exact-case lookup the cells apply) —
 * then the shared `STATUS_TONES` convention, else `brand`. Shared by the status widgets
 * and `StateTag` so a value colors the same wherever it renders.
 */
export function statusTone(
  value: string | null | undefined,
  override?: Record<string, Tone>,
  options: StatusToneOptions = {},
): Tone {
  const mapped = value && override ? override[value] : undefined;
  if (mapped) return mapped;
  const normalized = value?.trim().toLowerCase();
  if (!normalized) return options.emptyTone ?? "neutral";
  const tone = stateToneFromValue(normalized, STATUS_TONES);
  return tone === "brand" ? (options.unknownTone ?? "brand") : tone;
}
