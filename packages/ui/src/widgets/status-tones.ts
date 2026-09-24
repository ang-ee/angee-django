import { stateToneFromValue, type Tone, type ToneValueBuckets } from "../lib/tones";
import { optionToken } from "./types";

/**
 * The shared status vocabulary: which status-string values render in which tone. One
 * owner for "what color is this status", consumed by every status display — the
 * `statusBadge` pill, the `colorDot` dot, and the operator console's `StateTag` — so
 * they cannot drift (each previously kept its own divergent private map).
 *
 * These defaults are domain-neutral. Addons contribute product vocabulary through
 * their manifest's `statusTones`, composed into AppRuntime. Composition rejects
 * claims on framework defaults. React surfaces read the composed vocabulary through
 * `useStatusTone`. A caller's explicit
 * `<Column tone={{ VALUE: "tone" }}>` map wins over composed tones and these defaults.
 * An unmapped value falls to `brand` — the deliberate "unknown status" tone.
 *
 * Tones read as: success = live/healthy/done · warning = in-flight/needs-attention ·
 * danger = failed/hard-down · neutral = dormant/inert. The run-state axis the colored
 * dot shows maps stopped→neutral (grey), running→success (green), error→danger (red),
 * warning→warning (amber); see `docs/guidelines.md`.
 */
export const STATUS_TONES = {
  success: [
    "active", "connected", "published", "approved", "live", "open", "done",
    "running", "ready", "up", "online", "healthy", "completed",
    "succeeded", "won", "ok", "on_track", "complete", "confirmed",
  ],
  warning: [
    "draft", "paused", "review", "pending", "in_review",
    "provisioning", "deprovisioning", "starting", "connecting",
    "closed", "warning", "degraded", "waiting", "wait", "suspend",
    "at_risk", "escalated",
  ],
  danger: ["error", "failed", "denied", "lost", "down", "crashed", "off_track"],
  info: ["started", "assigned"],
  neutral: [
    "archived", "deleted", "disabled", "disconnected", "rejected", "blocked",
    "stopped", "deprovisioned", "idle", "inactive", "offline", "unknown", "default",
    "scheduled", "canceled", "skipped", "cancelled",
  ],
} satisfies ToneValueBuckets;

/** Addon-owned status values mapped to tones; composition normalizes value keys. */
export type StatusToneMap = Readonly<Record<string, Tone>>;

export interface StatusToneOptions {
  /** Composed addon vocabulary, keyed by normalized status value. */
  statusTones?: StatusToneMap;
  /** Tone for a non-empty value absent from the shared vocabulary. */
  unknownTone?: Tone;
  /** Tone for null/undefined/empty values. */
  emptyTone?: Tone;
}

/**
 * Resolve a status value's tone. The caller's explicit `<Column tone>` entry wins —
 * keyed on the value exactly as it reads (the same exact-case lookup the cells apply) —
 * then composed addon tones, the shared `STATUS_TONES` convention, else `brand`.
 * Pure transforms pass their vocabulary explicitly; React surfaces use `useStatusTone`.
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
  if (options.statusTones && Object.hasOwn(options.statusTones, normalized)) {
    const contributed = options.statusTones[normalized];
    if (contributed) return contributed;
  }
  const tone = stateToneFromValue(normalized, STATUS_TONES);
  return tone === "brand" ? (options.unknownTone ?? "brand") : tone;
}
