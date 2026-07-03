import * as React from "react";
import { useActionMutation } from "@angee/refine";
import {
  refineInvalidationParams,
  resourceInvalidationTargets,
  useSchemaFieldMetadata,
} from "@angee/metadata";

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
  const schemaMetadata = useSchemaFieldMetadata();
  const invalidates = React.useMemo(
    () =>
      resourceInvalidationTargets(
        schemaMetadata,
        options?.invalidateModels ?? [],
      ).map(refineInvalidationParams),
    [schemaMetadata, options?.invalidateModels],
  );
  const [mutate, state] = useActionMutation<TField>(field, {
    invalidates,
  });
  const run = React.useCallback<RecordActionRunner>((id) => mutate(id), [mutate]);
  return [useRecordAction(run, options), state];
}
