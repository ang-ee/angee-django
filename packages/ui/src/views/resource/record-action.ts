import * as React from "react";
import {
  extractActionOutcome,
  runActionResult,
  useActionMutation,
  type AuthoredDocument,
  type ActionArguments,
  type ActionMutate,
  type UseActionMutationState,
  type UseActionMutationOptions,
} from "@angee/refine";
import {
  useCanonicalResourceModelLabels,
  useResourceInvalidates,
} from "@angee/metadata";

import { useActionResultRun, useCreatedRecordLink } from "./action-result-run";
import { useRecordChromeContext } from "./record-chrome-context";
import { useAuthoredResourceMutation } from "./authored-resource-mutation";
import { useUiT } from "../../i18n";
import type { ActionContext, ActionResult } from "../page";

export type RecordActionRunner = (
  id: string,
  context: ActionContext,
) => ActionResult | Promise<ActionResult>;

export interface UseRecordActionOptions extends UseActionResultMutationOptions {
  /** Backend argument carrying the record id; defaults to `id`. */
  idArgument?: string;
  /** Extra generated action arguments derived from the current record id. */
  actionArguments?: (id: string) => ActionArguments;
  /** Message returned when the action itself returns none: nothing, or an empty text. */
  defaultMessage?: string;
  /** Error thrown when the form action is invoked before a saved record exists. */
  missingRecordMessage?: string;
  /** Refresh the form record after a successful run. Defaults to true. */
  refresh?: boolean;
  /** Extra invalidation after the record refresh is requested. */
  afterSuccess?: (
    context: ActionContext,
    result: ActionResult,
  ) => void | Promise<void>;
  /** Route a successful id-returning `ActionResult` to the created record. */
  linkTo?: string;
}

export type RecordAction = (context: ActionContext) => Promise<ActionResult>;

export function recordActionId(context: ActionContext): string | undefined {
  const id = context.record?.id;
  return typeof id === "string" && id !== "" ? id : undefined;
}

/** Build an `<Action run>` callback for actions that target the current record id.
 * @owner RecordActionBar
 */
export function useRecordAction(
  run: RecordActionRunner,
  options: UseRecordActionOptions = {},
): RecordAction {
  const {
    afterSuccess,
    defaultMessage,
    missingRecordMessage,
    refresh = true,
  } = options;
  return React.useCallback<RecordAction>(
    async (context) => {
      const id = recordActionId(context);
      if (!id) {
        if (missingRecordMessage) throw new Error(missingRecordMessage);
        return;
      }
      const result = (await run(id, context)) || defaultMessage;
      if (refresh) context.refresh();
      await afterSuccess?.(context, result);
      return result;
    },
    [afterSuccess, defaultMessage, missingRecordMessage, refresh, run],
  );
}

/**
 * Compose single-id `ActionResult` mutations into a record form action.
 *
 * The returned `run` callback can be passed directly to `<Action run={...} />`, and
 * the action bar toasts its outcome: a success resolves the message the bar shows
 * (`defaultMessage` when the server's is empty); a refusal rejects with its reasons
 * (`runActionResult`) and an answer with no outcome rejects too, each toasted under
 * the verb's label. A verb that returns the record it created is followed there
 * (`linkTo`), before the record refreshes.
 * @owner RecordActionBar
 */
export function useRecordActionMutation<TField extends string = string>(
  field: TField,
  options?: UseRecordActionOptions,
): [RecordAction, { fetching: boolean; error: Error | null }] {
  const [mutate, state] = useActionOutcomeMutation<TField>(field, options);
  const follow = useCreatedRecordLink(options?.linkTo);
  const t = useUiT();
  const run = React.useCallback<RecordActionRunner>(
    async (id) => {
      const arguments_ = options?.actionArguments?.(id);
      const outcome = await (arguments_ === undefined ? mutate(id) : mutate(id, arguments_));
      // A transport failure throws before this line; an answer that carries no outcome is told too.
      if (!outcome) throw new Error(t("action.noResult"));
      const message = runActionResult(outcome);
      follow(outcome);
      return message;
    },
    [follow, mutate, options?.actionArguments, t],
  );
  return [useRecordAction(run, options), state];
}

export interface UseActionResultMutationOptions extends Pick<UseActionMutationOptions, "transient" | "errorNotification"> {
  /** Backend argument carrying the record id; defaults to `id`. Null sends explicit arguments only. */
  idArgument?: string | null;
  /** Angee model labels whose refine caches this verb moves. */
  invalidateModels?: readonly string[];
  /** Schema that owns the target record; defaults to the ambient data provider. */
  dataProviderName?: string;
  /** The verb records its outcome even when it fails, so caches refresh after a failure too. */
  invalidateOnFailure?: boolean;
}

export type ActionResultMutation = (
  id: string,
  arguments_?: ActionArguments,
) => Promise<void>;

/**
 * Fire one generated id-targeted `ActionResult` mutation and settle its outcome.
 *
 * The ceremony a non-`<Action>` verb needs: resolve the mutated model labels'
 * refine `invalidates` through `@angee/metadata`'s {@link useResourceInvalidates},
 * fire by id, and settle through the shared `ActionResult` owner — which toasts
 * the server's own refusal message, including the in-band reasons an `ok=false`
 * outcome carries (it resolves rather than throws, so a bare `.catch()` would
 * surface nothing). A caller therefore renders no failure state of its own.
 *
 * Bound to no context, so it serves a verb rendered anywhere. A verb rendered in
 * record chrome takes {@link useRecordChromeActionMutation}, which reads the
 * record's own facts from that context instead of restating them here.
 * @owner ActionTrigger
 */
export function useActionResultMutation<TField extends string = string>(
  field: TField,
  options: UseActionResultMutationOptions = {},
): [ActionResultMutation, { fetching: boolean; error: Error | null }] {
  const [mutate, state] = useActionOutcomeMutation<TField>(field, options);
  const settle = useActionResultRun();
  const run = React.useCallback<ActionResultMutation>(
    async (id, arguments_) => {
      await settle(() =>
        arguments_ === undefined ? mutate(id) : mutate(id, arguments_),
      );
    },
    [mutate, settle],
  );
  return [run, state];
}

/**
 * Bind one generated action mutation with its model labels' refine invalidation
 * resolved through `@angee/metadata`, and hand back the raw `ActionOutcome`.
 *
 * The un-settled core of {@link useActionResultMutation}: a caller that owns
 * its own outcome handling — a typed-args `ActionFormDialog` binds
 * `validationErrors` to its inputs and toasts on `ok` itself — composes this
 * instead, so the outcome is neither toasted twice nor swallowed.
 * @owner ActionTrigger
 */
export function useActionOutcomeMutation<TField extends string = string>(
  field: TField,
  options: UseActionResultMutationOptions = {},
): [ActionMutate, UseActionMutationState] {
  const { dataProviderName } = options;
  const canonicalInvalidateModels = useCanonicalResourceModelLabels(
    options.invalidateModels,
  );
  const invalidates = useResourceInvalidates(canonicalInvalidateModels);
  return useActionMutation<TField>(field, {
    ...(options.idArgument !== undefined ? { idArgument: options.idArgument } : {}),
    ...(dataProviderName !== undefined ? { dataProviderName } : {}),
    ...(options.invalidateModels !== undefined
      ? { invalidateModels: canonicalInvalidateModels }
      : {}),
    ...(options.invalidateOnFailure ? { invalidateOnFailure: true } : {}),
    ...(options.transient !== undefined ? { transient: options.transient } : {}),
    ...(options.errorNotification !== undefined ? { errorNotification: options.errorNotification } : {}),
    invalidates,
  });
}

export interface UseRecordChromeActionMutationOptions extends Pick<UseActionMutationOptions, "transient" | "errorNotification"> {
  /** Argument carrying the targeted id when a slot acts on a related record. */
  idArgument?: string;
  /**
   * Further Angee model labels this verb writes. The record's own model and its
   * canonical MTI parent are always invalidated, so only name a *third* model.
   */
  invalidateModels?: readonly string[];
}

/**
 * Run one generated single-id `ActionResult` mutation from a record-chrome slot.
 *
 * {@link useRecordActionMutation} binds a form's *declared* `<Action run>` to its
 * `ActionContext` (record id + `refresh`); a contribution to a record-verb or
 * record-chrome slot has a `RecordChromeContext` instead. This reads that context
 * itself — the record's owning schema, its model, and the canonical MTI parent
 * whose caches the verb also moves — so a contributing addon declares only the
 * field it fires and never re-spells the mapping. It is
 * {@link useActionResultMutation} with those facts filled in from the context.
 * @owner RecordActionBar
 */
export function useRecordChromeActionMutation<TField extends string = string>(
  field: TField,
  options: UseRecordChromeActionMutationOptions = {},
): [ActionResultMutation, { fetching: boolean; error: Error | null }] {
  return useActionResultMutation<TField>(
    field,
    useRecordChromeActionOptions(options),
  );
}

/**
 * The typed-args counterpart of {@link useRecordChromeActionMutation}: the same
 * record-chrome binding, resolving the raw `ActionOutcome` instead of settling
 * it — for a contributed verb that collects arguments in an `ActionFormDialog`,
 * which owns binding the outcome's `validationErrors` and the success toast.
 */
export interface AuthoredRecordChromeAction {
  document: AuthoredDocument;
  resultField: string;
  idArgument: string;
}

/**
 * @owner RecordActionBar
 */
export function useRecordChromeActionOutcome<TField extends string = string>(
  field: TField,
  options?: UseRecordChromeActionMutationOptions,
): [ActionMutate, UseActionMutationState];
export function useRecordChromeActionOutcome(
  action: AuthoredRecordChromeAction,
  options?: UseRecordChromeActionMutationOptions,
): [ActionMutate, UseActionMutationState];
export function useRecordChromeActionOutcome(
  action: string | AuthoredRecordChromeAction,
  options: UseRecordChromeActionMutationOptions = {},
): [ActionMutate, UseActionMutationState] {
  const resolved = useRecordChromeActionOptions(options);
  // The declaration is fixed for a mounted contribution. Both transports
  // project the same ActionOutcome; their native hooks keep their own lifecycle.
  if (typeof action === "string") return useActionOutcomeMutation(action, resolved);
  return useAuthoredChromeActionOutcome(action, resolved);
}

function useAuthoredChromeActionOutcome(
  action: AuthoredRecordChromeAction,
  options: ReturnType<typeof useRecordChromeActionOptions>,
): [ActionMutate, UseActionMutationState] {
  const [mutate, state] = useAuthoredResourceMutation(action.document, {
    ...options,
    shouldInvalidate: (data) => extractActionOutcome(data, action.resultField)?.ok === true,
  });
  const run = React.useCallback<ActionMutate>(async (id, args = {}, callOptions) =>
    extractActionOutcome(
      await mutate({ ...args, [action.idArgument]: id } as never, callOptions),
      action.resultField,
    ) ?? undefined,
  [action.idArgument, action.resultField, mutate]);
  return [run, state];
}

/** Read the record-chrome context into the action options a chrome verb fires with. */
function useRecordChromeActionOptions(
  options: UseRecordChromeActionMutationOptions,
): UseActionResultMutationOptions {
  const { canonicalResource, dataProviderName, resource } =
    useRecordChromeContext();
  // Derived during render: `useResourceInvalidates` keys on the labels'
  // contents, so a fresh array identity here costs nothing downstream.
  const invalidateModels = [
    ...new Set([resource, canonicalResource, ...(options.invalidateModels ?? [])]),
  ];
  return {
    ...(dataProviderName !== undefined ? { dataProviderName } : {}),
    ...(options.idArgument !== undefined ? { idArgument: options.idArgument } : {}),
    ...(options.transient !== undefined ? { transient: options.transient } : {}),
    ...(options.errorNotification !== undefined ? { errorNotification: options.errorNotification } : {}),
    invalidateModels,
  };
}
