import { stateToneFromValue, type Tone, type ToneValueBuckets } from "../lib/tones";
import { optionToken } from "./types";

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
 * warning→warning (amber); see `docs/guidelines.md`.
 */
export const STATUS_TONES: ToneValueBuckets = {
  success: [
    "active", "connected", "published", "approved", "live", "open", "done",
    "running", "ready", "up", "online", "healthy", "completed",
    "succeeded", "won", "ok",
    // Document lifecycle (accounting/sales): a posted/paid/confirmed/invoiced
    // document has reached its healthy terminal state.
    "posted", "paid", "confirmed", "invoiced",
  ],
  warning: [
    "draft", "paused", "review", "pending", "in_review",
    "provisioning", "deprovisioning", "starting", "connecting",
    "closed", "warning", "degraded", "waiting",
    // A work-stage category that is explicitly asking for a human decision.
    "triage",
    // Document lifecycle: awaiting money or an invoice — in-flight, needs attention.
    "not_paid", "partial", "to_invoice",
  ],
  danger: ["error", "failed", "denied", "lost", "down", "crashed"],
  // Work-stage categories (`work.Stage.category`) read on the same axis as the
  // statuses above: `started` is already in-flight blue, `completed` already
  // green. `backlog` and `unstarted` are the not-yet-picked-up greys, `triage`
  // the one that wants a human.
  info: ["started", "assigned"],
  neutral: [
    "archived", "deleted", "disabled", "disconnected", "rejected", "blocked",
    "stopped", "deprovisioned", "idle", "inactive", "offline", "unknown", "default",
    "scheduled", "canceled", "skipped",
    // Work-stage categories that mean "not picked up yet" / "not real work".
    "backlog", "unstarted", "duplicate",
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
 * Resolve a status value's tone. The caller's explicit `<Column tone>` entry wins,
 * then the shared `STATUS_TONES` convention, else `brand`. Shared by the status widgets
 * and `StateTag` so a value colors the same wherever it renders.
 *
 * The override is matched exactly first, then on the {@link optionToken} — the same
 * two-step {@link canonicalOptionValue} applies to options, and for the same reason: a
 * GraphQL enum reads back as its member *name* (`OPEN`), while an author writing a tone
 * map spells the backend's own token (`open`). Matching only exactly made the override
 * silently inert for every enum field read over GraphQL.
 */
export function statusTone(
  value: string | null | undefined,
  override?: Record<string, Tone>,
  options: StatusToneOptions = {},
): Tone {
  const mapped = value && override ? override[value] : undefined;
  if (mapped) return mapped;
  const normalized = optionToken(value);
  if (!normalized) return options.emptyTone ?? "neutral";
  const normalizedOverride = override?.[normalized];
  if (normalizedOverride) return normalizedOverride;
  const tone = stateToneFromValue(normalized, STATUS_TONES);
  return tone === "brand" ? (options.unknownTone ?? "brand") : tone;
}
