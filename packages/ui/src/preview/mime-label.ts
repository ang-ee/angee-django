import { useCallback } from "react";

import { useUiT } from "../i18n";
import { normaliseMime } from "./model";

/** The human kinds a MIME type reads as; the suffixes of the `mime.<kind>` i18n keys.
 * @kind token
 */
export const MIME_KINDS = [
  "pdf", "image", "video", "audio", "archive", "spreadsheet", "presentation", "document",
  "text", "markdown", "json", "xml", "csv", "html", "code", "calendar", "contact", "email", "binary",
] as const;

export type MimeKind = (typeof MIME_KINDS)[number];

const EXACT: Record<string, MimeKind> = {
  "application/pdf": "pdf",
  "text/markdown": "markdown",
  "text/x-markdown": "markdown",
  "application/json": "json",
  "text/json": "json",
  "application/xml": "xml",
  "text/xml": "xml",
  "text/csv": "csv",
  "text/html": "html",
  "text/calendar": "calendar",
  "text/vcard": "contact",
  "text/x-vcard": "contact",
  "message/rfc822": "email",
  "application/zip": "archive",
  "application/gzip": "archive",
  "application/x-tar": "archive",
  "application/x-7z-compressed": "archive",
  "application/vnd.rar": "archive",
  "application/msword": "document",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",
  "application/vnd.oasis.opendocument.text": "document",
  "application/rtf": "document",
  "application/vnd.ms-excel": "spreadsheet",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "spreadsheet",
  "application/vnd.oasis.opendocument.spreadsheet": "spreadsheet",
  "application/vnd.ms-powerpoint": "presentation",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "presentation",
  "application/vnd.oasis.opendocument.presentation": "presentation",
  "text/plain": "text",
  "application/octet-stream": "binary",
};

/** The kind a MIME type reads as, from its exact type, else its family.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function mimeKind(mime: string | null | undefined): MimeKind {
  const normalised = normaliseMime(mime);
  const exact = EXACT[normalised];
  if (exact) return exact;
  if (normalised.startsWith("image/")) return "image";
  if (normalised.startsWith("video/")) return "video";
  if (normalised.startsWith("audio/")) return "audio";
  if (normalised.startsWith("text/x-") || normalised.endsWith("+json") || normalised.startsWith("application/x-shellscript")) return "code";
  if (normalised.endsWith("+xml")) return "xml";
  if (normalised.startsWith("text/")) return "text";
  return "binary";
}

/**
 * The human label of a MIME type ("PDF document", "Image", "Spreadsheet"): the one
 * owner of that vocabulary, for file lists, facts lines and message parts. A
 * registered type label (a storage mime type's own) wins over it at the caller.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function useMimeLabel(): (mime: string | null | undefined) => string {
  const t = useUiT();
  return useCallback((mime) => t(`mime.${mimeKind(mime)}`), [t]);
}
