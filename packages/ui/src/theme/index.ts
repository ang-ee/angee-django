import type { ComponentType } from "react";
import type { ColorScheme, ThemeDefinition, ThemeOptionsEnvelope } from "./runtime.mjs";

export * from "./runtime.mjs";

export interface ThemePreviewProps { definition: ThemeDefinition<unknown>; colorScheme: ColorScheme; options?: ThemeOptionsEnvelope }
export interface ThemeOptionsEditorProps { definition: ThemeDefinition<unknown>; value: ThemeOptionsEnvelope | undefined; disabled?: boolean; onChange: (value: ThemeOptionsEnvelope) => void }
export interface ThemeContribution { definition: ThemeDefinition<unknown>; preview?: ComponentType<ThemePreviewProps>; optionsEditor?: ComponentType<ThemeOptionsEditorProps> }

export function defineThemeContribution<TOptions>(contribution: { definition: ThemeDefinition<TOptions>; preview?: ComponentType<ThemePreviewProps>; optionsEditor?: ComponentType<ThemeOptionsEditorProps> }): ThemeContribution {
  return contribution as ThemeContribution;
}

export * from "./appearance";
export * from "./preview";
