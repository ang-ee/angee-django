import type { ReactNode } from "react";
import type { CrudFilter } from "@refinedev/core";
import type { Row } from "@angee/metadata";

import { PAGE_ELEMENT_SLOT } from "./types";
import type { LabelKey } from "../../lib/labels";
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
  /**
   * Concrete list/create resource for a relation whose stored model is a base
   * type. The field still reads and writes its declared relation id; only the
   * picker is narrowed to this compatible subtype (for example Party ->
   * Organization).
   */
  relationResource?: string;
  /** Server-side filters narrowing the rows offered by a relation picker. */
  filters?: readonly CrudFilter[];
  widget?: string;
  /** Concrete GraphQL leaf paths selected when `name` is an object or list its widget renders itself. */
  selectionPaths?: readonly string[];
  /** Adapt a stored value to a widget's control type without changing form values. */
  valueCodec?: {
    toControl: (value: unknown) => unknown;
    fromControl: (value: unknown) => unknown;
  };
  readOnly?: boolean;
  /**
   * Require a value independently of generated model create metadata. A field with no presence
   * flag, template or bound fails `required` when it is empty: `null`, `undefined`, a blank text or
   * an empty list. Inside a structured field (one with a presence flag, a template or a bound) and
   * for each of its members, `required` means the value must be present: absent, `undefined` or a
   * `null` the field does not allow fails it, while a blank text or an empty list is a value the
   * descriptor's own bounds judge.
   */
  required?: boolean;
  /** A required structured value may explicitly be JSON null. */
  nullable?: boolean;
  /**
   * The value must be present, as a JSON property is: absent, `undefined` or a `null` the field
   * does not allow fails `required` even where the field is not `required`, and the flag makes the
   * field structured, so a blank text or an empty list in it is a value, not a missing one.
   * Emitted only by structured FormSpec, beside `required` for a required property.
   */
  presenceRequired?: boolean;
  /**
   * The value may be left "Not set": the member is then absent from the saved object. Emitted only
   * by structured FormSpec. In a field of its own, choosing "Not set" writes an own key holding
   * `undefined`; a member the record never had, and one dropped from the draft, have no key. React
   * Hook Form replaces a "Not set" by the form's default whenever the field registers again (an
   * undo or redo replay, a pane that unmounts). Before such a member is shown as its own field in
   * a pane that unmounts, or undo is bound in a record form, it must stop being a registered field
   * of its own: `useFormHistory` states the measured shape, what the form must then supply, and the
   * tests that watch for it.
   */
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
  /** Render and submit this field only when the predicate matches form values, save what a changed preset writes for it (see `prefill`). */
  showWhen?: (values: Row) => boolean;
  /** Keep this field in form values and mutation data without rendering a control. */
  hidden?: boolean;
  /**
   * Presentation that follows the form's current values (the chosen implementation's, say): everything
   * it returns is laid over this declaration, except `readOnly`, which stays set when either sets it.
   * It returns this descriptor's own type without `name`: a dialog field's may return dialog members
   * alone, and a subtype's own required members are returned with it. A type derived from a
   * descriptor by `Omit` or `Pick`, the `<Field>` element's props among them, returns the base
   * descriptor's. It cannot rename the field, which keeps reading and writing its declared `name`:
   * the compiler refuses a returned value whose type carries a string `name` (a literal giving one, a
   * spread of a whole descriptor, a value typed as a descriptor), and `resolveField` ignores a name
   * that gets past it (a value typed without `name` or loosely, as `Record`, `object` or `any`; a
   * cast; JavaScript).
   */
  resolve?: (values: Row) => Omit<this, "name"> & { name?: never };
  /**
   * The preset this field loads onto sibling fields when it changes, from its value, the form's
   * current values and the values the form was loaded with (this field's scope in a nested form),
   * so a preset may derive from siblings or give a sibling its loaded value back; in a record form
   * the targets it hides are dropped from the draft.
   * On save, each preset field whose value changed writes its preset's value for every target that
   * is hidden now and was loaded with a different value, in declaration order (the later wins).
   * `prefill` therefore also runs at save and must be pure.
   */
  prefill?: (value: unknown, values: Row, loaded?: Row) => Record<string, unknown> | null | undefined;
  /** Keep dirty sibling values when applying a preset, except for names explicitly replaced below. */
  prefillPreserveDirty?: boolean;
  /**
   * The preset fields `prefillPreserveDirty` still replaces when dirty (private implementation
   * configuration, say), and the only ones it writes on save for a hidden target; without
   * `prefillPreserveDirty` a preset replaces every field it names.
   */
  prefillReplace?: readonly string[];
  /** Source field a `widget="slug"` field derives from on create (see `FieldProps.slugFrom`). */
  slugFrom?: string;
  /** Render a saved field beside the title; on create it remains in the form. */
  placement?: "title";
  title?: boolean;
  /** Place this field above the record heading. */
  status?: boolean;
  /** Occupy the available record status row; interpreted by status widgets. */
  fill?: boolean;
  /** Width measured by the record status row for responsive status widgets. */
  containerWidth?: number;
  /** Main record body; `document` renders continuous prose without a visible field label or box. */
  body?: boolean | "document";
  kind?: PageFieldKind;
  description?: ReactNode;
}

/**
 * JSX declaration shape and resolved descriptor share one lifecycle contract. A
 * declared label may be an addon message key; the form surface resolves it to
 * text before any descriptor renders.
 */
export interface FieldProps extends Omit<FieldDescriptor, "label"> {
  label?: ReactNode | LabelKey;
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

/**
 * Whether a field is a to-many relation multi-select (`many2many`), which holds
 * related records or their ids and submits the public id list.
 */
export function isRelationListField(field: FieldDescriptor): boolean {
  return fieldWidgetId(field) === "many2many";
}

function FieldMarker(_props: FieldProps): null {
  return null;
}

/**
 * @kind contribution
 */
export const Field = Object.assign(FieldMarker, {
  [PAGE_ELEMENT_SLOT]: "field" as const,
});
