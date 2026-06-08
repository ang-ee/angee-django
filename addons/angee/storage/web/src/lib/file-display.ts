import {
  isImageMime,
  isJsonMime,
  isMarkdownMime,
  isTextOrCodeMime,
  type BadgeVariant,
} from "@angee/base";

// Presentational mappings for file rows: a mime → glyph name, the upload-state
// → stage badge, and a compact date. Byte sizes reuse `formatSize` from
// `@angee/base` (the preview model owns it) — this module never re-coins it.

/** Registry glyph for a file's mime; `image`/`folder` are registered by the addon. */
export function fileIconName(mime: string | null | undefined): string {
  if (!mime) return "file";
  if (isImageMime(mime)) return "image";
  if (isMarkdownMime(mime) || isJsonMime(mime) || isTextOrCodeMime(mime)) {
    return "file";
  }
  return "file";
}

export interface FileStage {
  label: string;
  variant: BadgeVariant;
}

/** Map the byte-lifecycle state to a stage badge. Case-insensitive: the enum
 * may arrive as the member name or the stored value. */
export function fileStage(uploadState: string): FileStage {
  switch (uploadState.toLowerCase()) {
    case "ready":
      return { label: "Ready", variant: "success" };
    case "draft":
      return { label: "Uploading", variant: "warning" };
    case "failed":
      return { label: "Failed", variant: "danger" };
    default:
      return { label: uploadState || "Unknown", variant: "default" };
  }
}

/** A short, locale-formatted date, or an em dash when absent/invalid. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
