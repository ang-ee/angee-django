import * as React from "react";
import {
  runActionResult,
  useActionMutation,
  type ActionArguments,
} from "@angee/refine";
import { useResourceInvalidates } from "@angee/metadata";

import {
  useActionResultRun,
  type ActionResultRunOptions,
} from "./action-result-run";
import { useRecordChromeContext } from "./record-chrome-context";
import type { ActionContext, ActionResult } from "./page";

export type RecordActionRunner = (
  id: string,
  context: ActionContext,
) => ActionResult | Promise<ActionResult>;

export interface UseRecordActionOptions {
  /** Message returned when the action itself returns no message. */
  defaultMessage?: string;
  /** Extra Angee model labels whose refine caches this action mutates. */
  invalidateModels?: readonly string[];
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
  /** Let the shared ActionResult owner toast and optionally deep-link the result. */
  settle?: boolean | ActionResultRunOptions;
}

export type RecordAction = (context: ActionContext) => Promise<ActionResult>;

export function recordActionId(context: ActionContext): string | undefined {
  const id = context.record?.id;
  return typeof id === "string" && id !== "" ? id : undefined;
}

/** Build an `<Action run>` callback for actions that target the current record id. */
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
      const result = (await run(id, context)) ?? defaultMessage;
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
 * The returned `run` callback can be passed directly to `<Action run={...} />`.
 */
export function useRecordActionMutation<TField extends string = string>(
  field: TField,
  options?: UseRecordActionOptions,
): [RecordAction, { fetching: boolean; error: Error | null }] {
  const invalidates = useResourceInvalidates(options?.invalidateModels);
  const [mutate, state] = useActionMutation<TField>(field, {
    ...(options?.invalidateModels !== undefined
      ? { invalidateModels: options.invalidateModels }
      : {}),
    invalidates,
  });
  const settleOptions = React.useMemo<ActionResultRunOptions | null>(() => {
    if (options?.settle === false) return null;
    if (options?.settle && typeof options.settle === "object") {
      return {
        ...(options.linkTo ? { linkTo: options.linkTo } : {}),
        ...options.settle,
      };
    }
    if (options?.linkTo) return { linkTo: options.linkTo };
    return options?.settle === true ? {} : null;
  }, [options?.linkTo, options?.settle]);
  const settleActionResult = useActionResultRun(settleOptions ?? undefined);
  // Legacy callers project the in-band outcome to the rendered `<Action run>`
  // contract. Id-returning bridge actions delegate toast/link settling to the
  // shared ActionResult owner so consumer hooks stay declarative.
  const run = React.useCallback<RecordActionRunner>(
    async (id) => {
      if (settleOptions) {
        await settleActionResult(() => mutate(id));
        return undefined;
      }
      return runActionResult(await mutate(id));
    },
    [mutate, settleActionResult, settleOptions],
  );
  return [useRecordAction(run, options), state];
}

export interface UseActionResultMutationOptions {
  /** Angee model labels whose refine caches this verb moves. */
  invalidateModels?: readonly string[];
  /** Schema that owns the target record; defaults to the ambient data provider. */
  dataProviderName?: string;
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
 */
export function useActionResultMutation<TField extends string = string>(
  field: TField,
  options: UseActionResultMutationOptions = {},
): [ActionResultMutation, { fetching: boolean; error: Error | null }] {
  const { dataProviderName } = options;
  const invalidates = useResourceInvalidates(options.invalidateModels);
  const [mutate, state] = useActionMutation<TField>(field, {
    ...(dataProviderName !== undefined ? { dataProviderName } : {}),
    ...(options.invalidateModels !== undefined
      ? { invalidateModels: options.invalidateModels }
      : {}),
    invalidates,
  });
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

export interface UseRecordChromeActionMutationOptions {
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
 */
export function useRecordChromeActionMutation<TField extends string = string>(
  field: TField,
  options: UseRecordChromeActionMutationOptions = {},
): [ActionResultMutation, { fetching: boolean; error: Error | null }] {
  const { canonicalResource, dataProviderName, resource } =
    useRecordChromeContext();
  // Derived during render: `useResourceInvalidates` keys on the labels'
  // contents, so a fresh array identity here costs nothing downstream.
  const invalidateModels = [
    ...new Set([resource, canonicalResource, ...(options.invalidateModels ?? [])]),
  ];
  return useActionResultMutation<TField>(field, {
    ...(dataProviderName !== undefined ? { dataProviderName } : {}),
    invalidateModels,
  });
}
