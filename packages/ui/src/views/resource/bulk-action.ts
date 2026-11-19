import * as React from "react";
import { useInvalidate } from "@refinedev/core";
import { useQueryClient } from "@tanstack/react-query";
import { invalidateAuthoredQueries } from "@angee/refine";
import { refineResourceName, type DataResourceMetadata, type ModelMetadata, type Row } from "@angee/metadata";

import { errorMessage, useToast } from "../../feedback";
import { useUiT } from "../../i18n";
import { useLatestRef } from "../../lib/use-latest-ref";
import { useRuntimeViewAs } from "../../runtime";

/** Maximum rows previewed or written by one generic selection action.
 * @kind null
 * @reason Runtime infrastructure: bounds the selection's client fan-out.
 */
export const BULK_ACTION_LIMIT = 25;
const BULK_ACTION_CONCURRENCY = 5;

export interface BulkSelectionProps {
  modelMetadata: ModelMetadata;
  selectedRows: readonly Row[];
  onSucceeded: (ids: readonly string[]) => void;
}

/** Native settled promises, in bounded batches; entries retain selection order.
 * @kind null
 * @reason Runtime infrastructure: bounded concurrency and per-row outcomes for bulk actions.
 */
export async function settleBulkRows<T>(
  ids: readonly string[],
  perform: (id: string) => Promise<T>,
): Promise<{ id: string; result: PromiseSettledResult<T> }[]> {
  const bounded = ids.slice(0, BULK_ACTION_LIMIT);
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
  perform: (id: string) => Promise<void>;
  /** One confirmation for the bounded selection, before any writes. */
  confirm?: () => Promise<boolean>;
  done: (count: number) => string;
  failed: (count: number) => string;
}

/**
 * Settle a bounded fan-out over a resource's single-record verb. Callers disable
 * per-row notifications/invalidation and reject refused or missing outcomes.
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
  const queryClient = useQueryClient();
  const preview = useRuntimeViewAs();
  const viewAs = useLatestRef(preview);
  const running = React.useRef(false);
  const [pending, setPending] = React.useState(false);
  const blocked = Boolean(preview.viewAs || preview.pending);
  const run = React.useCallback(async ({ ids, perform, confirm, done, failed }: BulkActionOptions) => {
    if (!resource || ids.length === 0 || running.current || viewAs.current.viewAs || viewAs.current.pending) return;
    running.current = true;
    setPending(true);
    try {
      if (confirm && !await confirm()) return;
      if (viewAs.current.viewAs || viewAs.current.pending) return;
      const results = await settleBulkRows(ids, async (id) => {
        if (viewAs.current.viewAs || viewAs.current.pending) throw new Error(t("viewAs.readOnly"));
        await perform(id);
      });
      const succeeded = results.filter((entry) => entry.result.status === "fulfilled").map((entry) => entry.id);
      const reasons = results.flatMap((entry) => entry.result.status === "rejected"
        ? [errorMessage(entry.result.reason, t("action.noResult"))] : []);
      const details = [
        ...(reasons.length ? [failed(reasons.length), reasons[0]!] : []),
        ...(ids.length > results.length ? [t("selection.bulkRemaining", { count: ids.length - results.length })] : []),
      ];
      if (succeeded.length) {
        onSucceeded(succeeded);
        try {
          await Promise.all([
            invalidate({ resource: refineResourceName(resource), dataProviderName: resource.schemaName,
              invalidates: ["list", "many", "detail"] }),
            invalidateAuthoredQueries(queryClient, [resource.modelLabel]),
          ]);
        } catch (error) {
          details.push(errorMessage(error, t("selection.bulkRefreshFailed")));
        }
      }
      const message = { title: done(succeeded.length), ...(details.length ? { description: details.join(" ") } : {}) };
      if (details.length) toast.warning(message);
      else toast.success(message);
    } finally {
      running.current = false;
      setPending(false);
    }
  }, [invalidate, onSucceeded, queryClient, resource, t, toast, viewAs]);
  return { run, pending, blocked };
}
