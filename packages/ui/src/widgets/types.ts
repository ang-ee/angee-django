import type { ComponentType, ReactNode } from "react";

import type { Tone } from "../lib/tones";

export interface WidgetOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

/**
 * The label for an option `value`: the matching option's `label`, else the raw
 * value, else "". The one owner of the
 * `options.find(o => o.value === v)?.label ?? v ?? ""` lookup the scalar and
 * relation widgets each re-spelled.
 */
export function optionLabel(
  options: readonly WidgetOption[] | undefined,
  value: string | null | undefined,
): ReactNode {
  return options?.find((option) => option.value === value)?.label ?? value ?? "";
}

export function optionTextLabel(value: ReactNode): string | undefined;
export function optionTextLabel(value: ReactNode, fallback: string): string;
export function optionTextLabel(
  value: ReactNode,
  fallback?: string,
): string | undefined {
  if (typeof value === "string" || typeof value === "number") return String(value);
  return fallback;
}

export interface WidgetField {
  name?: string;
  label?: ReactNode;
  options?: readonly WidgetOption[];
  /** Explicit `value → Tone` map (from `<Column tone>`) for status widgets. */
  tone?: Record<string, Tone>;
}

export interface WidgetRenderProps<TValue = unknown, TRow = unknown> {
  value?: TValue | null;
  row?: TRow;
  field?: WidgetField;
  readOnly?: boolean;
  onChange?: (value: TValue) => void;
}

export interface WidgetDefinition<TValue = unknown, TRow = unknown> {
  edit?: ComponentType<WidgetRenderProps<TValue, TRow>>;
  read: ComponentType<WidgetRenderProps<TValue, TRow>>;
  cell?: ComponentType<WidgetRenderProps<TValue, TRow>>;
}
