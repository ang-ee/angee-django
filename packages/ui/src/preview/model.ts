import type { PreviewFile } from "./registry";

// Pure mime/preview helpers — no heavy renderer dependency lives here.

const DEFAULT_MIME = "application/octet-stream";

/** Extension → mime for files whose `mime` is unknown. */
const EXT_MIME: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  svg: "image/svg+xml",
  avif: "image/avif",
  heic: "image/heic",
  heif: "image/heif",
  pdf: "application/pdf",
  md: "text/markdown",
  markdown: "text/markdown",
  json: "application/json",
  txt: "text/plain",
  csv: "text/csv",
  html: "text/html",
  mp4: "video/mp4",
  webm: "video/webm",
  mov: "video/quicktime",
  mp3: "audio/mpeg",
  wav: "audio/wav",
  js: "text/javascript",
  ts: "text/typescript",
  tsx: "text/tsx",
  jsx: "text/jsx",
  py: "text/x-python",
  go: "text/x-go",
  rs: "text/x-rust",
  sql: "text/x-sql",
  sh: "application/x-shellscript",
  yaml: "text/x-yaml",
  yml: "text/x-yaml",
  css: "text/css",
  xml: "text/xml",
};

/** Extension → highlight language hint. */
const EXT_LANGUAGE: Record<string, string> = {
  js: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  py: "python",
  go: "go",
  rs: "rust",
  sql: "sql",
  sh: "bash",
  yaml: "yaml",
  yml: "yaml",
  json: "json",
  css: "css",
  html: "html",
  xml: "xml",
  md: "markdown",
};

function extension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** Lower-cased mime with parameters stripped; the empty/unknown becomes octet. */
export function normaliseMime(value: string | null | undefined): string {
  if (!value) return DEFAULT_MIME;
  return value.split(";")[0]?.trim().toLowerCase() || DEFAULT_MIME;
}

/** The file's content type, falling back to its extension then octet-stream. */
export function displayMime(file: PreviewFile): string {
  const declared = normaliseMime(file.mime);
  if (declared !== DEFAULT_MIME) return declared;
  return EXT_MIME[extension(file.name)] ?? DEFAULT_MIME;
}

export function isImageMime(mime: string): boolean {
  return mime.startsWith("image/");
}
/**
 * HEIC/HEIF images — a subset of `isImageMime` that browsers (bar Safari) cannot
 * decode, so they need a dedicated renderer rather than a plain `<img>`.
 */
export function isHeicMime(mime: string): boolean {
  return (
    mime === "image/heic" ||
    mime === "image/heif" ||
    mime === "image/heic-sequence" ||
    mime === "image/heif-sequence"
  );
}
export function isMarkdownMime(mime: string): boolean {
  return mime === "text/markdown" || mime === "text/x-markdown";
}
export function isJsonMime(mime: string): boolean {
  return mime === "application/json" || mime.endsWith("+json");
}
export function isTextOrCodeMime(mime: string): boolean {
  return mime.startsWith("text/") || isJsonMime(mime);
}

/** A highlight language hint for a file (by extension). */
export function languageForFile(file: PreviewFile): string {
  return EXT_LANGUAGE[extension(file.name)] ?? "text";
}

/** A human file size (e.g. "1.4 MB"). */
export function formatSize(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value % 1 === 0 ? value : value.toFixed(1)} ${units[unit]}`;
}
