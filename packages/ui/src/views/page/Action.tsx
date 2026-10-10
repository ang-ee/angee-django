import type { ReactNode } from "react";
import type { Row } from "@angee/metadata";
import type { ActionOutcome } from "@angee/refine";
import type { CrudFilter } from "@refinedev/core";
import type { Resolver } from "react-hook-form";

import type { PromptOptions } from "../../feedback";
import type { DialogSize } from "../../ui/dialog";
import type { DescriptorField } from "../form/DescriptorFieldList";
import type { FormSubmitResult } from "../form/validation-errors";
import type { RelationCreateConfig } from "../relation/RelationPicker";
import type { FieldDescriptor } from "./Field";
import { PAGE_ELEMENT_SLOT } from "./types";

export interface ActionConfirm {
  title: ReactNode;
  body?: ReactNode;
  danger?: boolean;
}

/**
 * Context handed to an action's imperative `run` callback.
 *
 * `values` are a snapshot for the duration of `run`; `record` remains live.
 */
export interface ActionContext {
  /** The open record the action targets (`null` while creating). */
  record: Row | null;
  /** Values collected by the action's `prompt`, keyed by field name. */
  values: Record<string, string>;
  /** Re-pull the target record into the form (fire-and-forget). */
  refresh: () => void;
  /** Patch the target record through the model's generated `update` mutation. */
  update: (patch: Record<string, unknown>) => Promise<Row | null>;
  /** Open a follow-up prompt — e.g. to reveal a freshly rotated secret. */
  prompt: (options: PromptOptions) => Promise<Record<string, string> | null>;
}

/** A non-empty string is shown as a success toast; `void` shows none. */
export type ActionResult = string | void;

/**
 * Context handed to a typed-args action form: the record the action was invoked
 * on and the ids selected on the invoking surface (the open record's own id on a
 * record bar; the checked rows on a list). A `relationList` arg prefills from it.
 */
export interface ActionFormContext {
  /** The open record the action targets, or `null` on a list/create surface. */
  record: Row | null;
  /** Public ids of the invoking model the action targets: the open record or the acted-on row. */
  selectedIds: readonly string[];
  /** Refetch the invoking record without replacing this action's collected draft. */
  refresh?: () => Promise<Row | null>;
}

/** A descriptor form, including record-specific JSON Schema argument forms. */
export interface ActionFormDefinition {
  fields: readonly DescriptorField[] | ((values: Record<string, unknown>) => readonly DescriptorField[]);
  /** Use the shared dialog size tokens for forms with retained context. */
  size?: DialogSize;
  defaultValues?: Record<string, unknown>;
  resolver?: Resolver<Record<string, unknown>>;
  /** All possible fields, including inactive branches, for server issue binding. */
  fieldNames?: readonly string[];
  /** Consumer editors rendered inside the same React Hook Form provider. */
  content?: ReactNode;
}

export type ActionArgs = readonly ActionArg[] | ActionFormDefinition;
export type ActionSubmitResult = ActionOutcome | FormSubmitResult<ActionOutcome> | null | undefined;

/**
 * Base shape of one typed action argument: the slice of the `FieldDescriptor`
 * vocabulary an action form actually renders (widget resolution, options,
 * label/placeholder/description) — form-lifecycle fields (`showWhen`, `prefill`,
 * `createOnly`, …) have no meaning for an action arg and are excluded.
 */
interface ActionArgBase extends Pick<
  FieldDescriptor,
  | "name"
  | "label"
  | "widget"
  | "kind"
  | "options"
  | "placeholder"
  | "description"
  | "currencyField"
  | "statusDisplay"
  | "defaultValue"
> {
  /** Not required before the form may submit (e.g. an optional amount). */
  optional?: boolean;
}

/** A scalar arg (date, number/money, switch, enum select, text). The default kind. */
export interface ActionScalarArg extends ActionArgBase {
  argKind?: "scalar";
  /** Seed from the invoking context when the dialog opens; user edits take precedence. */
  fromContext?: (context: ActionFormContext) => unknown;
}

/** A single relation-picker arg naming the target resource its options list. */
export interface ActionRelationArg extends ActionArgBase {
  argKind: "relation";
  /** Seed the saved relation from the invoking record when the dialog opens. */
  fromContext?: (context: ActionFormContext) => unknown;
  /** Target model label the picker lists (as `useModelMetadata` resolves it). */
  resource: string;
  /** Server-side filters narrowing the relation rows offered by the picker. */
  filters?: readonly CrudFilter[];
  /**
   * Inline create forwarded to the picker (`RelationPicker.create`): a no-match
   * typed query offers "Create …", `actionLabel` also exposes the form as a
   * visible button, and the saved record becomes the selection.
   */
  create?: RelationCreateConfig;
}

/**
 * A multi relation-list arg, empty unless `fromContext` seeds it (explicit edit wins).
 * The action form submits de-duplicated, non-empty string ids.
 */
export interface ActionRelationListArg extends ActionArgBase {
  argKind: "relationList";
  /** Target model label the picker lists (as `useModelMetadata` resolves it). */
  resource: string;
  /** Server-side filters narrowing the rows offered by this multi-picker. */
  filters?: readonly CrudFilter[];
  /**
   * Inline create forwarded to the multi-picker (`RelationPicker.create`): a
   * visible "New …" button (or `actionLabel`) opens the related model's create
   * form, and the saved record joins the selection.
   */
  create?: RelationCreateConfig;
  /**
   * Prefill the selected ids from the invoking context; without it the list
   * starts empty. A list of the invoking model seeds itself with
   * `({ selectedIds }) => selectedIds`. A user edit overrides the prefill.
   */
  fromContext?: (context: ActionFormContext) => readonly string[];
}

/** One typed argument collected by an action form — scalar, relation, or relation list. */
export type ActionArg =
  ActionScalarArg | ActionRelationArg | ActionRelationListArg;

interface ActionBinding {
  /**
   * Declarative field patch applied to the target record via the model's
   * generated `update` mutation — e.g. `set={{ isEnabled: false }}` to toggle, or
   * `set={{ status: "REVOKED" }}` to revoke. Merged with any `prompt` values.
   */
  set?: Record<string, unknown>;
  /** Collect input before the action runs (reset a password, reveal a secret). */
  prompt?: PromptOptions;
  /** Imperative escape hatch for a custom (non-CRUD) mutation. */
  run?: (context: ActionContext) => ActionResult | Promise<ActionResult>;
  /**
   * Typed-args form (F-a): open a dialog collecting these args, merge them with
   * the invoking record/selection context (explicit edit wins), then fire
   * `submit`. Ignored without `submit`; a `submit` verb without args opens the
   * same dialog with none.
   */
  args?: ActionArgs | ((context: ActionFormContext) => ActionArgs);
  /**
   * Fire the authored mutation for the action form and return its in-band
   * `ActionOutcome` (compose `@angee/refine`'s `useAuthoredMutation` +
   * `extractActionOutcome`). The dialog binds `validationErrors` to the args and
   * stays open until `ok`; on `ok` it toasts `message` and closes. The collected
   * values are keyed by arg `name`. A `FormSubmitResult` can additionally report
   * a stale-record conflict, locking the draft until the dialog is reopened.
   * A `null`/`undefined` outcome — the shape the
   * outcome extractors return when the response carries no envelope — is a
   * form-level failure, so a caller passes it through rather than inventing one.
   */
  submit?: (
    values: Record<string, unknown>,
    context: ActionFormContext,
  ) => ActionSubmitResult | Promise<ActionSubmitResult>;
}

export interface ActionProps extends ActionBinding {
  id: string;
  label: ReactNode;
  /** Projected permission required to offer this record verb. */
  permission?: string;
  icon?: string;
  disabled?: boolean;
  /** Explain a record action's disabled state beside its toolbar button; a function reads it from the loaded record and disables while it returns one. */
  disabledReason?: ReactNode | ((record: Row) => ReactNode);
  /** A destructive verb: danger-toned, and confirmed before it runs on click (standard copy unless `confirm` names it). */
  danger?: boolean;
  /** Promote a frequent record verb out of the default Actions menu. */
  placement?: "menu" | "toolbar";
  /** The page's one primary verb: rendered as the primary button when it is on the toolbar and visible. */
  primary?: boolean;
  /** Static confirmation copy, or copy derived from the loaded record; a danger verb's copy takes the danger tone. */
  confirm?: ActionConfirm | ((record: Row) => ActionConfirm);
  /**
   * Show this action only when the open record matches — e.g. show "Disable"
   * only while enabled. Evaluated against the loaded record; an action with a
   * predicate is hidden until a record is open.
   */
  visibleWhen?: (record: Row) => boolean;
}

/** The parsed form of an `<Action>` — identical to its props. */
export type ActionDescriptor = ActionProps;

function ActionMarker(_props: ActionProps): null {
  return null;
}

export const Action = Object.assign(ActionMarker, {
  [PAGE_ELEMENT_SLOT]: "action" as const,
});
