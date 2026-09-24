import { formatDate as formatBaseDate } from "@angee/ui";

// Presentational mappings for file rows: the upload-state → stage badge and a
// date display. Byte sizes reuse `formatSize` from `@angee/ui` (the preview
// model owns it) — this module never re-coins it.

/** Map the byte-lifecycle state to its stage label. Case-insensitive: the enum
 * may arrive as the member name or the stored value. `t` is threaded in from the
 * rendering component (this module is not a component). */
export function fileStageLabel(
  uploadState: string,
  t: (key: string) => string,
): string {
  switch (uploadState.toLowerCase()) {
    case "ready":
      return t("stage.ready");
    case "draft":
      return t("stage.uploading");
    case "failed":
      return t("stage.failed");
    default:
      return uploadState || t("stage.unknown");
  }
}

/** A base-formatted date, or an em dash when absent/invalid. */
export function formatDate(value: string | null | undefined): string {
  return formatBaseDate(value) || "—";
}
