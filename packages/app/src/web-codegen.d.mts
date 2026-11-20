import type { ThemeDefinition } from "@angee/ui/theme-runtime";
import type { SiteHead } from "./shell-settings.mjs";

/** Declared generation inputs, resolved through workspace symlinks. */
export interface WebCodegenInputs {
  files: string[];
  globs: string[];
  directories: string[];
  /** Generated outputs, even when a declared document glob would match them. */
  excluded: string[];
}

export interface WebCodegenOptions {
  webRoot?: string;
  runtimeDir?: string;
  /** Vite supplies its native SSR loader for fresh modules during development. */
  loadThemeModule?: (entry: string) => Promise<{ themes?: unknown }>;
  /** Incremental discovery keeps missing or malformed inputs retryable. */
  onInputs?: (inputs: WebCodegenInputs) => void;
}

/**
 * A document an addon package serves beside the app, declared in its
 * `web/package.json` `angee.pages` and built as `<id>.html` at the output root.
 */
export interface StandalonePage {
  id: string;
  /** The declaring web package. */
  package: string;
  /** Absolute canonical entry inside that package. */
  file: string;
  /** URL path prefixes the page answers, each ending in `/*`. */
  paths: readonly string[];
  /** Source of the pattern a request's `Accept` header must match: the manifest's `pageAccept` for the page's media type. */
  acceptPattern: string;
}

export interface WebCodegenResult {
  inputs: WebCodegenInputs;
  themes: readonly ThemeDefinition<unknown>[];
  /** Declared standalone pages in id order, their entries resolved inside their packages. */
  pages: readonly StandalonePage[];
  /** The content security policy the ingress sends every page: the manifest's `pageCsp`, when it publishes one. */
  pageCsp?: string;
  /** The build-only static head, its selected assets resolved to absolute canonical files. */
  head?: SiteHead;
}

/** Generate GraphQL documents and the composed frontend runtime once. */
export function generateWebRuntime(options?: WebCodegenOptions): Promise<WebCodegenResult>;
