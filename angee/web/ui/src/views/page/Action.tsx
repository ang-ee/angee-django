import type { ReactNode } from "react";
import type { Row } from "@angee/resources";

import type { PromptOptions } from "../../feedback";
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
