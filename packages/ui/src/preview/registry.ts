import type { ComponentType } from "react";
import type { PreviewContribution } from "../runtime";

import { normaliseMime } from "./model";

/**
 * The preview contract. A `PreviewFile` is a file reduced to what any renderer
 * needs (a URL + name + optional mime/size); addons adapt their domain model
 * (e.g. storage's `File`) into this shape before handing it to `PreviewPane`.
 * Renderers declare a mime pattern; `PreviewPane` resolves the highest-priority
 * match from the built-ins plus any addon-contributed providers (composed at
 * build time onto the runtime via the manifest `previews` field).
 */
export interface PreviewFile {
  /** Resolved display/fetch URL. */
  url: string;
  /** File name — drives extension detection and download. */
  name: string;
  /** Content type (may be null/unknown). */
  mime?: string | null;
  /** Size in bytes. */
  size?: number | null;
  /** Opaque renderer-specific payload. */
  metadata?: unknown;
}

export interface PreviewProviderProps {
  file: PreviewFile;
  /** Normalised mime resolved by `PreviewPane` (always a concrete string). */
  mime: string;
}

export type PreviewProviderComponent = ComponentType<PreviewProviderProps>;

/**
 * A mime matcher: an exact type ("image/png"), a "type/" glob prefix, the
 * "every mime" wildcard, a RegExp, or a predicate.
 */
export type PreviewMimeMatcher =
  | string
  | RegExp
  | ((mime: string) => boolean);

export interface PreviewProvider extends PreviewContribution {
  mime: PreviewMimeMatcher;
  component: PreviewProviderComponent;
  /** Higher wins when several providers match; defaults to 0. */
  priority?: number;
}

/**
 * The highest-priority provider whose matcher accepts `mime`, or null. Pure over
 * the passed list — `PreviewPane` calls it with the built-ins plus the runtime's
 * addon-contributed providers. Sort is stable, so on a priority tie an earlier
 * entry wins (callers list runtime providers before the built-ins to let an
 * addon override a built-in at equal priority).
 */
export function resolvePreviewProvider(
  providers: readonly PreviewProvider[],
  mime: string | null | undefined,
): PreviewProvider | null {
  const normalized = normaliseMime(mime);
  return (
    providers
      .filter((provider) => matchesMime(provider.mime, normalized))
      .sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0))[0] ?? null
  );
}

function matchesMime(pattern: PreviewMimeMatcher, mime: string): boolean {
  if (typeof pattern === "function") return pattern(mime);
  if (pattern instanceof RegExp) return pattern.test(mime);
  if (pattern === "*/*") return true;
  if (pattern.endsWith("/*")) return mime.startsWith(pattern.slice(0, -1));
  return mime === pattern.toLowerCase();
}
