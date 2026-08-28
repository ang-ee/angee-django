import { formatDate as formatBaseDate, statusTone as resolveStatusTone, type Tone } from "@angee/ui";

// Presentational mappings for file rows: the upload-state → stage badge and a
// date display. Byte sizes reuse `formatSize` from `@angee/ui` (the preview
// model owns it) — this module never re-coins it.

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
  const tone = resolveStatusTone(uploadState, undefined, {
    unknownTone: "neutral",
  });
  switch (uploadState.toLowerCase()) {
    case "ready":
      return { label: t("stage.ready"), tone };
    case "draft":
      return { label: t("stage.uploading"), tone };
    case "failed":
      return { label: t("stage.failed"), tone };
    default:
      return { label: uploadState || t("stage.unknown"), tone };
  }
}

/** A base-formatted date, or an em dash when absent/invalid. */
export function formatDate(value: string | null | undefined): string {
  return formatBaseDate(value) || "—";
}
