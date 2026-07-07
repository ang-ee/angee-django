import type { ComponentType, ReactNode } from "react";

import type { Tone } from "../lib/tones";

export interface WidgetOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

/**
 * Extract the scalar id a relation widget reads and writes. Refine/Hasura detail
 * reads may carry a nested related record (`{ id }`) while write inputs expect
 * the flat public id.
 */
export function relationValueId(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  const id = (value as { id?: unknown }).id;
  return typeof id === "string" || typeof id === "number" ? String(id) : "";
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

/**
 * Match a scalar option value back to the authored option value. Direct matches
 * win; a unique case-insensitive match covers GraphQL enum reads such as
 * `ANTHROPIC` when mutation inputs use the lower-case value `anthropic`.
 */
export function canonicalOptionValue(
  options: readonly WidgetOption[] | undefined,
  value: unknown,
): string | undefined {
  if (typeof value !== "string" || !options || options.length === 0) {
    return undefined;
  }
  const direct = options.find((option) => option.value === value);
  if (direct) return direct.value;
  const lower = value.toLowerCase();
  const matches = options.filter((option) => option.value.toLowerCase() === lower);
  return matches.length === 1 ? matches[0]?.value : undefined;
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
  placeholder?: string;
  /** Explicit `value → Tone` map (from `<Column tone>`) for status widgets. */
  tone?: Record<string, Tone>;
  /**
   * For a money widget: the path to the FK owning the row's currency — a sibling
   * field (`"currency"`) or a one-hop related path (`"order.currency"`). Carried
   * from the backend field metadata so the money renderer resolves the row's
   * currency from the record without re-deriving it.
   */
  currencyField?: string;
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
