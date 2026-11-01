import { useCallback } from "react";
import { refineResourceName, rowPublicId, useModelMetadata, useResourceInvalidates, type Row } from "@angee/metadata";
import { useReconcileMutationResponse } from "@angee/refine";
import { useUpdate, type BaseRecord, type HttpError } from "@refinedev/core";
import { useAuthoredResourceMutation, useLatestRef } from "@angee/ui";
import { KnowledgeCreatePage, PAGE_MODEL } from "./documents";

export interface PageActions {
  busy: boolean;
  createPage: (input: { vault: string; title: string; kind: string; parent: string | null }) => Promise<string | null>;
  movePage: (id: string, parent: string | null) => Promise<void>;
}

/** Navigator writes compose authored creation and the resource's hierarchy update. */
export function usePageActions(): PageActions {
  const resource = useModelMetadata(PAGE_MODEL)?.resource;
  const reconcile = useReconcileMutationResponse();
  const invalidates = useResourceInvalidates([PAGE_MODEL],
    (_data: unknown, input: { id?: string | number }) => [{ model: PAGE_MODEL, id: String(input.id) }]);
  const [create, createState] = useAuthoredResourceMutation(KnowledgeCreatePage, { invalidateModels: [PAGE_MODEL] });
  const update = useUpdate<BaseRecord & Row, HttpError, { parent: string | null }>({
    resource: refineResourceName(resource ?? null), dataProviderName: resource?.schemaName,
    invalidates: [],
    mutationOptions: { onSuccess: (result, input) => reconcile(result.data, input, {
      records: [{ model: PAGE_MODEL, id: String(input.id) }], invalidates,
    }) },
  });
  const latest = useLatestRef({ create, update: update.mutateAsync });
  const createPage = useCallback<PageActions["createPage"]>(async (input) =>
    rowPublicId((await latest.current.create(input))?.create_page), []);
  const movePage = useCallback<PageActions["movePage"]>(async (id, parent) => {
    await latest.current.update({ id, values: { parent } });
  }, []);
  return { busy: createState.fetching || update.mutation.isPending, createPage, movePage };
}
