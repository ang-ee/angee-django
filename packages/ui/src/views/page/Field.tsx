import type { ReactNode } from "react";
import type { Row } from "@angee/metadata";

import { PAGE_ELEMENT_SLOT } from "./types";
import type { FieldPresentation } from "../../widgets/types";

export type PageFieldKind =
  | "text"
  | "textarea"
  | "select"
  | "switch"
  | "readonly"
  | "selection"
  | (string & {});

export interface FieldDescriptor extends FieldPresentation {
  name: string;
  widget?: string;
  readOnly?: boolean;
  /** Require a value independently of generated model create metadata. */
  required?: boolean;
  /** A required structured value may explicitly be JSON null. */
  nullable?: boolean;
  /** Treat required as JSON property presence; emitted only by structured FormSpec. */
  presenceRequired?: boolean;
  omittable?: boolean;
  hasDefault?: boolean;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  minItems?: number;
  maxItems?: number;
  objectTemplate?: readonly FieldDescriptor[];
  itemTemplate?: FieldDescriptor;
  /** Editable only while creating; read-only (and never patched) on an edit. */
  createOnly?: boolean;
  /** Editable only while editing; read-only (and never sent) on a create. */
  editOnly?: boolean;
  /** Create-form seed for this field, submitted even when `readOnly`/`createOnly` (see `FieldProps.defaultValue`). */
  defaultValue?: unknown;
  /** Render and submit this field only when the predicate matches form values (see `FieldProps`). */
  showWhen?: (values: Row) => boolean;
  /** Keep this field in form values and mutation data without rendering a control. */
  hidden?: boolean;
  /** Resolve implementation-dependent presentation from current form values. */
  resolve?: (values: Row) => FieldDescriptor;
  /** Load the chosen preset onto sibling fields when this field changes (see `FieldProps.prefill`). */
  prefill?: (value: unknown) => Record<string, unknown> | null | undefined;
  /** Keep dirty sibling values when applying a preset, except for names explicitly replaced below. */
  prefillPreserveDirty?: boolean;
  /** Fields a preset must replace even when they are dirty (for example private implementation config). */
  prefillReplace?: readonly string[];
  /** Source field a `widget="slug"` field derives from on create (see `FieldProps.slugFrom`). */
  slugFrom?: string;
  title?: boolean;
  body?: boolean;
  kind?: PageFieldKind;
  description?: ReactNode;
}

/** JSX declaration shape and resolved descriptor share one lifecycle contract. */
export interface FieldProps extends FieldDescriptor {}

/**
 * The widget id a field descriptor resolves to: its explicit `widget`, else its
 * `kind`, else the `text` fallback. The descriptor owns this resolution so a
 * view never re-derives "which widget is this field" from its shape.
 */
export function fieldWidgetId(field: FieldDescriptor): string {
  // Truthy (not nullish) so an empty `widget` string falls through to `kind`.
  return field.widget || field.kind || "text";
}

/**
 * Whether a field is a scalar-id relation picker (`many2one`), which selects the
 * related node's `<name>.id` and submits/clears as a relation id. A field-shape
 * fact the descriptor answers about itself.
 */
export function isRelationIdField(field: FieldDescriptor): boolean {
  return fieldWidgetId(field) === "many2one";
}

function FieldMarker(_props: FieldProps): null {
  return null;
}

export const Field = Object.assign(FieldMarker, {
  [PAGE_ELEMENT_SLOT]: "field" as const,
});
