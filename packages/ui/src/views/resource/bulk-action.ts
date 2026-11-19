import * as React from "react";
import { useInvalidate } from "@refinedev/core";
import { useQueryClient } from "@tanstack/react-query";
import { invalidateAuthoredQueries } from "@angee/refine";
import { useResourceInvalidates, type DataResourceMetadata, type ModelMetadata, type Row } from "@angee/metadata";

import { errorMessage, useToast } from "../../feedback";
import { useUiT } from "../../i18n";
import { useLatestRef } from "../../lib/use-latest-ref";
import { useRuntimeViewAs } from "../../runtime";

/** Maximum rows previewed or written by one client fan-out per click.
 * Kept below the server action_selection_limit (100): each row sends a separate
 * request, and Delete sends both a preview and a confirmed request.
 * @kind null
 * @reason Runtime infrastructure: bounds the selection's client fan-out.
 */
export const BULK_ACTION_FANOUT_LIMIT = 25;
const BULK_ACTION_CONCURRENCY = 5;

export interface BulkSelectionProps {
  modelMetadata: ModelMetadata;
  selectedRows: readonly Row[];
  /** Selected ids with no observed row cannot be admitted to a flag action. */
  unprocessedCount: number;
  bulk: BulkActionRunner;
  /** Why the verbs cannot run for this selection (all records the list matches, not its ticked rows). */
  disabledReason?: string;
}

export interface BulkActionRefusal {
  status: "refused";
  reason: string;
}

export type BulkActionRunner = ReturnType<typeof useBulkAction>;

/** Native settled promises, in bounded batches; entries retain selection order.
 * @kind null
 * @reason Runtime infrastructure: bounded concurrency and per-row outcomes for bulk actions.
 */
export async function settleBulkRows<T>(
  ids: readonly string[],
  perform: (id: string) => Promise<T>,
): Promise<{ id: string; result: PromiseSettledResult<T> }[]> {
  const bounded = ids.slice(0, BULK_ACTION_FANOUT_LIMIT);
  const entries: { id: string; result: PromiseSettledResult<T> }[] = [];
  for (let start = 0; start < bounded.length; start += BULK_ACTION_CONCURRENCY) {
    const batch = bounded.slice(start, start + BULK_ACTION_CONCURRENCY);
    const results = await Promise.allSettled(batch.map(async (id) => perform(id)));
    results.forEach((result, index) => entries.push({ id: batch[index]!, result }));
  }
  return entries;
}

interface BulkActionOptions {
  ids: readonly string[];
  perform: (id: string) => Promise<void | BulkActionRefusal>;
  unprocessedCount?: number;
  /** One confirmation for the bounded selection, before any writes. */
  confirm?: () => Promise<boolean>;
  done: (count: number) => string;
  failed: (count: number) => string;
}

/**
 * Settle a bounded fan-out over a resource's single-record verb. Callers disable
 * per-row notifications/invalidation, return server refusals and reject failures.
 * Only successful ids leave selection; caches refresh once and one toast reports
 * successes, refusals (with the first reason), and rows left for another pass.
 * @owner ListView
 */
export function useBulkAction(
  resource: DataResourceMetadata | null | undefined,
  onSucceeded: (ids: readonly string[]) => void,
) {
  const t = useUiT();
  const toast = useToast();
  const invalidate = useInvalidate();
  const invalidates = useResourceInvalidates(resource ? [resource.modelLabel] : undefined);
  const queryClient = useQueryClient();
  const preview = useRuntimeViewAs();
  const viewAs = useLatestRef(preview);
  const running = React.useRef(false);
  const mounted = React.useRef(false);
  React.useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const [pending, setPending] = React.useState(false);
  const blocked = Boolean(preview.viewAs || preview.pending);
  const run = React.useCallback(async ({ ids, perform, confirm, done, failed, unprocessedCount = 0 }: BulkActionOptions) => {
    if (!mounted.current || !resource || ids.length === 0 || running.current || viewAs.current.viewAs || viewAs.current.pending) return;
    running.current = true;
    setPending(true);
    try {
      if (confirm && !await confirm()) return;
      if (!mounted.current || viewAs.current.viewAs || viewAs.current.pending) return;
      const results = await settleBulkRows(ids, async (id) => {
        if (!mounted.current || viewAs.current.viewAs || viewAs.current.pending) throw new Error(t("viewAs.readOnly"));
        return perform(id);
      });
      if (!mounted.current) return;
      const succeeded = results.filter((entry) => entry.result.status === "fulfilled" && !entry.result.value).map((entry) => entry.id);
      const refusals = results.flatMap((entry) => entry.result.status === "fulfilled" && entry.result.value
        ? [entry.result.value.reason] : []);
      const failures = results.flatMap((entry) => entry.result.status === "rejected"
        ? [errorMessage(entry.result.reason, t("action.noResult"))] : []);
      const remaining = unprocessedCount + ids.length - results.length;
      const details = [
        ...(refusals.length ? [t("selection.bulkRefusedDetail", { count: refusals.length, reason: refusals[0]! })] : []),
        ...(failures.length ? [t("selection.bulkFailedDetail", { count: failures.length, reason: failures[0]! })] : []),
        ...(remaining ? [t("selection.bulkRemaining", { count: remaining })] : []),
      ];
      if (succeeded.length) {
        onSucceeded(succeeded);
        try {
          await Promise.all([
            ...invalidates.map((target) => invalidate(target)),
            invalidateAuthoredQueries(queryClient, [resource.modelLabel]),
          ]);
        } catch (error) {
          details.push(errorMessage(error, t("selection.bulkRefreshFailed")));
        }
      }
      if (!mounted.current) return;
      const title = succeeded.length ? done(succeeded.length)
        : refusals.length ? t("selection.bulkRefused", { count: refusals.length }) : failed(failures.length);
      const message = { title, ...(details.length ? { description: details.join(" ") } : {}) };
      if (details.length) toast.warning(message);
      else toast.success(message);
    } finally {
      running.current = false;
      if (mounted.current) setPending(false);
    }
  }, [invalidate, invalidates, onSucceeded, queryClient, resource, t, toast, viewAs]);
  return { run, pending, blocked };
}
