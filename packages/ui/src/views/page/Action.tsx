import type { ReactNode } from "react";
import type { Row } from "@angee/metadata";
import type { ActionOutcome } from "@angee/refine";

import type { PromptOptions } from "../../feedback";
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
 * `record` and `values` are a snapshot for the duration of `run`. `refresh` is
 * fire-and-forget — it re-pulls the record into the form but does not update the
 * `record` already captured here; read fresh state from your mutation's own
 * result (or `update`'s) rather than awaiting `refresh`.
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
  /** Public ids selected on the invoking surface — a `relationList` arg's default. */
  selectedIds: readonly string[];
}

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
> {
  /** Not required before the form may submit (e.g. an optional amount). */
  optional?: boolean;
}

/** A scalar arg (date, number/money, switch, enum select, text). The default kind. */
export interface ActionScalarArg extends ActionArgBase {
  argKind?: "scalar";
}

/** A single relation-picker arg naming the target resource its options list. */
export interface ActionRelationArg extends ActionArgBase {
  argKind: "relation";
  /** Target model label the picker lists (as `useModelMetadata` resolves it). */
  resource: string;
}

/** A multi relation-list arg, prefilled from the invoking context (explicit edit wins). */
export interface ActionRelationListArg extends ActionArgBase {
  argKind: "relationList";
  /** Target model label the picker lists (as `useModelMetadata` resolves it). */
  resource: string;
  /**
   * Prefill the selected ids from the invoking context. Defaults to the invoking
   * selection, else the open record's id. A user edit overrides the prefill.
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
   * `submit`. Ignored without `submit`.
   */
  args?: readonly ActionArg[];
  /**
   * Fire the authored mutation for an `args` form and return its in-band
   * `ActionOutcome` (compose `@angee/refine`'s `useAuthoredMutation` +
   * `extractActionOutcome`). The dialog binds `validationErrors` to the args and
   * stays open until `ok`; on `ok` it toasts `message` and closes. The collected
   * values are keyed by arg `name`.
   */
  submit?: (
    values: Record<string, unknown>,
    context: ActionFormContext,
  ) => ActionOutcome | Promise<ActionOutcome>;
}

export interface ActionProps extends ActionBinding {
  id: string;
  label: ReactNode;
  icon?: string;
  disabled?: boolean;
  danger?: boolean;
  confirm?: ActionConfirm;
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
