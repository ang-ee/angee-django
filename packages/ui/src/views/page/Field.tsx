import type { ReactNode } from "react";
import type { Row } from "@angee/metadata";

import { PAGE_ELEMENT_SLOT } from "./types";
import type { WidgetOption } from "../../widgets/types";

export type PageFieldKind =
  | "text"
  | "textarea"
  | "select"
  | "switch"
  | "readonly"
  | "selection"
  | (string & {});

export interface FieldProps {
  name: string;
  label?: ReactNode;
  widget?: string;
  readOnly?: boolean;
  /** Editable only while creating; read-only (and never patched) on an edit. */
  createOnly?: boolean;
  /** Editable only while editing; read-only (and never sent) on a create. */
  editOnly?: boolean;
  /**
   * Render (and submit) this field only when the predicate matches the form's
   * current values — the form's discriminated-field mechanism. Mirrors
   * `Action.visibleWhen`, but evaluates against live form values so a `kind`
   * select can swap the body. A hidden field is never sent.
   */
  showWhen?: (values: Row) => boolean;
  /**
   * Seed sibling fields when this field's value changes — the impl-defaults
   * mechanism. Returns a `{fieldName: value}` map (camelCase form field names) the
   * form applies as the chosen preset, overwriting those fields (so boolean defaults
   * land too). Pair with `useImplPrefill(model, field)` for an `ImplClassField`.
   * Mark the field `createOnly` when saved rows must not be re-seeded; leave it
   * editable when changing the impl should rematerialize defaults in the form.
   */
  prefill?: (value: unknown) => Record<string, unknown> | null | undefined;
  /**
   * For a `widget="slug"` field: the form field this slug auto-derives from while
   * creating (lowercased + hyphenated), until the user edits the slug. Defaults to
   * the record's `title` field. The derive runs in the form, not the backend.
   */
  slugFrom?: string;
  title?: boolean;
  body?: boolean;
  kind?: PageFieldKind;
  options?: readonly WidgetOption[];
  placeholder?: string;
  description?: ReactNode;
}

export interface FieldDescriptor {
  name: string;
  label?: ReactNode;
  widget?: string;
  readOnly?: boolean;
  /** Editable only while creating; read-only (and never patched) on an edit. */
  createOnly?: boolean;
  /** Editable only while editing; read-only (and never sent) on a create. */
  editOnly?: boolean;
  /** Render and submit this field only when the predicate matches form values (see `FieldProps`). */
  showWhen?: (values: Row) => boolean;
  /** Load the chosen preset onto sibling fields when this field changes (see `FieldProps.prefill`). */
  prefill?: (value: unknown) => Record<string, unknown> | null | undefined;
  /** Source field a `widget="slug"` field derives from on create (see `FieldProps.slugFrom`). */
  slugFrom?: string;
  title?: boolean;
  body?: boolean;
  kind?: PageFieldKind;
  options?: readonly WidgetOption[];
  placeholder?: string;
  description?: ReactNode;
  /** Money widget: path to the FK owning the row's currency (see `WidgetField.currencyField`). */
  currencyField?: string;
}

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
