import type { ComponentType, ReactNode } from "react";

import type { ActionOutcome, AuthoredDocument } from "@angee/refine";
import type { Tone } from "../lib/tones";

/** A verb binding; allowed values are returned by the record's authorization owner. */
export interface VisibilityBinding {
  allowedValues: readonly string[];
  onSelect: (value: string) => Promise<ActionOutcome | null | undefined>;
  disabled?: boolean;
}

/** A field's server visibility verb, with argument names declared by its addon. */
export interface VisibilityAction {
  document: AuthoredDocument;
  resultField: string;
  idArgument: string;
  revisionArgument?: string;
  /** Verb enum values may differ from create-input values. */
  options?: readonly WidgetOption[];
  /** Readable server projection carrying the current audience's human label. */
  audienceField?: string;
}

export interface WidgetOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
  /** A statusbar owner may mark a terminal or side option outside its path. */
  onPath?: boolean;
  /** Server-owned status choices declare eligibility; plain form options default to selectable. */
  selectable?: boolean;
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

/** Normalize related records or scalar ids to de-duplicated, non-empty public ids. */
export function relationIdList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map(relationValueId))].filter(Boolean);
}

/**
 * The label for an option value. GraphQL enum member names resolve against
 * authored input values through the same canonical matching rule as selects.
 */
export function optionLabel(
  options: readonly WidgetOption[] | undefined,
  value: string | null | undefined,
): ReactNode {
  const canonical = canonicalOptionValue(options, value);
  return options?.find((option) => option.value === canonical)?.label ?? value ?? "";
}

/**
 * The comparable token for an enum-ish value: trimmed and lower-cased, `""` for
 * anything that is not a string.
 *
 * A GraphQL enum reads back as its member *name* (`CONNECTED`, `WHATSAPP`) while
 * the code comparing it spells the backend's own lower-case token (`connected`,
 * `whatsapp`), so a read is compared through this rule rather than matched
 * exactly. The one owner of that rule: `canonicalOptionValue` applies it when an
 * authored option list is available to resolve against, `statusTone` against the
 * status vocabulary, and a caller with neither compares tokens directly.
 */
export function optionToken(value: unknown): string {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}

/**
 * Match a scalar option value back to the authored option value. Direct matches
 * win; a unique case-insensitive match ({@link optionToken}) covers GraphQL enum
 * reads such as `ANTHROPIC` when mutation inputs use the lower-case value
 * `anthropic`.
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
  const lower = optionToken(value);
  const matches = options.filter(
    (option) => optionToken(option.value) === lower,
  );
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

/** Presentation facts shared by page descriptors and rendered widget fields. */
export interface FieldPresentation {
  /** IAM assignment-subject controls offer these native subject kinds. */
  /** Statusbar layout; the form slot may supply measured width at render time. */
  fill?: boolean;
  containerWidth?: number;
  visibilityAction?: VisibilityAction;
  label?: ReactNode;
  options?: readonly WidgetOption[];
  placeholder?: string;
  /**
   * For a money widget: the path to the FK owning the row's currency — a sibling
   * field (`"currency"`) or a one-hop related path (`"order.currency"`).
   */
  currencyField?: string;
}


export interface WidgetField extends FieldPresentation {
  name?: string;
  fill?: boolean;
  containerWidth?: number;
  /** Explicit `value → Tone` map (from `<Column tone>`) for status widgets. */
  tone?: Record<string, Tone>;
  /** DOM association supplied by a descriptor-form owner for its actual control. */
  controlProps?: WidgetControlProps;
}

export interface WidgetControlProps {
  id: string;
  "aria-describedby"?: string;
  "aria-labelledby"?: string;
  "aria-required"?: boolean;
  "aria-invalid"?: boolean;
  min?: number;
  max?: number;
  minLength?: number;
  maxLength?: number;
}

export interface WidgetFocusTarget {
  focus(): void;
}

export interface WidgetRenderProps<TValue = unknown, TRow = unknown> {
  value?: TValue | null;
  /** Current sibling values in forms, or the source record in read/list views. */
  row?: TRow;
  /** Owning document for a widget rendered inside editable child lines. */
  parentRow?: unknown;
  field?: WidgetField;
  /** Validation messages scoped to this widget's descriptor field. */
  messages?: readonly string[];
  readOnly?: boolean;
  /** Temporarily lock a mounted editor while retaining its local draft. */
  disabled?: boolean;
  onChange?: (value: TValue) => void;
  /** Atomically patch sibling fields of this editable line; stale rows are ignored. */
  onRowChange?: (patch: Record<string, unknown>) => void;
  /** Signal that the widget's current user interaction has completed. */
  onCommit?: () => void;
  /** Report whether the current editor draft can be committed. */
  onValidityChange?: (valid: boolean) => void;
  /** The widget's actual interactive control or trigger. */
  controlRef?: (target: WidgetFocusTarget | null) => void;
}

export interface WidgetDefinition<TValue = unknown, TRow = unknown> {
  /** Accepts a parsed `rowTemplate` for fixed-size arrays of objects in a form spec. */
  acceptsRowTemplate?: true;
  edit?: ComponentType<WidgetRenderProps<TValue, TRow>>;
  read: ComponentType<WidgetRenderProps<TValue, TRow>>;
  cell?: ComponentType<WidgetRenderProps<TValue, TRow>>;
}
