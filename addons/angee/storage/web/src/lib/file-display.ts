import { isImageMime, type Tone } from "@angee/base";

// Presentational mappings for file rows: a mime → glyph name, the upload-state
// → stage badge, and a compact date. Byte sizes reuse `formatSize` from
// `@angee/base` (the preview model owns it) — this module never re-coins it.

/** Registry glyph for a file's mime — `image` (addon-registered) or the
 * base `file` fallback. */
export function fileIconName(mime: string | null | undefined): string {
  return mime && isImageMime(mime) ? "image" : "file";
}

export interface FileStage {
  label: string;
  tone: Tone;
}

/** Map the byte-lifecycle state to a stage badge. Case-insensitive: the enum
 * may arrive as the member name or the stored value. `t` is threaded in from the
 * rendering component (this module is not a component). */
export function fileStage(
  uploadState: string,
  t: (key: string) => string,
): FileStage {
  switch (uploadState.toLowerCase()) {
    case "ready":
      return { label: t("storage.stage.ready"), tone: "success" };
    case "draft":
      return { label: t("storage.stage.uploading"), tone: "warning" };
    case "failed":
      return { label: t("storage.stage.failed"), tone: "danger" };
    default:
      return { label: uploadState || t("storage.stage.unknown"), tone: "neutral" };
  }
}

/** A short, locale-formatted date, or an em dash when absent/invalid. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
