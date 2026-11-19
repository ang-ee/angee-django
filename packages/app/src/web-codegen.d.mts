import type { ThemeDefinition } from "@angee/ui/theme-runtime";

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

export interface WebCodegenResult {
  inputs: WebCodegenInputs;
  themes: readonly ThemeDefinition<unknown>[];
  /** Build-only static head facts; selected asset paths are absolute canonical files. */
  site?: {
    title: string;
    description?: string;
    siteName?: string;
    icon?: string;
    touchIcon?: string;
    image?: string;
  };
}

/** Generate GraphQL documents and the composed frontend runtime once. */
export function generateWebRuntime(options?: WebCodegenOptions): Promise<WebCodegenResult>;
