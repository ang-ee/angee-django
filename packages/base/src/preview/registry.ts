import type { ComponentType } from "react";

import { normaliseMime } from "./model";

/**
 * The preview contract. A `PreviewFile` is a file reduced to what any renderer
 * needs (a URL + name + optional mime/size); addons adapt their domain model
 * (e.g. storage's `File`) into this shape before handing it to `PreviewPane`.
 * Renderers register against a mime pattern; `PreviewPane` resolves the
 * highest-priority match. The registry ships here (the contract); heavy
 * renderers register against it from their own module.
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

export interface PreviewProvider {
  id: string;
  mime: PreviewMimeMatcher;
  component: PreviewProviderComponent;
  /** Higher wins when several providers match; defaults to 0. */
  priority?: number;
}

const providers = new Map<string, PreviewProvider>();

/**
 * Register-or-replace by id, gated by priority: a higher-priority registration
 * replaces a lower one with the same id; equal/lower is skipped. One slot per
 * id keeps HMR re-registrations from stacking.
 */
export function registerPreviewProvider(provider: PreviewProvider): void {
  const current = providers.get(provider.id);
  if (!current || (provider.priority ?? 0) >= (current.priority ?? 0)) {
    providers.set(provider.id, provider);
  }
}

export function resolvePreviewProvider(
  mime: string | null | undefined,
): PreviewProvider | null {
  const normalized = normaliseMime(mime);
  return (
    [...providers.values()]
      .filter((provider) => matchesMime(provider.mime, normalized))
      .sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0))[0] ?? null
  );
}

export function previewProviders(): readonly PreviewProvider[] {
  return [...providers.values()].sort(
    (a, b) => (b.priority ?? 0) - (a.priority ?? 0),
  );
}

/** Reset the registry; for tests only. */
export function clearPreviewProvidersForTest(): void {
  providers.clear();
}

function matchesMime(pattern: PreviewMimeMatcher, mime: string): boolean {
  if (typeof pattern === "function") return pattern(mime);
  if (pattern instanceof RegExp) return pattern.test(mime);
  if (pattern === "*/*") return true;
  if (pattern.endsWith("/*")) return mime.startsWith(pattern.slice(0, -1));
  return mime === pattern.toLowerCase();
}
