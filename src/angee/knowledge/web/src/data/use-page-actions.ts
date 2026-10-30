import { useCallback } from "react";
import { refineResourceName, rowPublicId, useModelMetadata, type Row } from "@angee/metadata";
import { useInvalidateAuthoredModels } from "@angee/refine";
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
  const invalidate = useInvalidateAuthoredModels();
  const [create, createState] = useAuthoredResourceMutation(KnowledgeCreatePage, { invalidateModels: [PAGE_MODEL] });
  const update = useUpdate<BaseRecord & Row, HttpError, { parent: string | null }>({
    resource: refineResourceName(resource ?? null), dataProviderName: resource?.schemaName,
    invalidates: ["list", "many", "detail"],
  });
  const latest = useLatestRef({ create, update: update.mutateAsync, invalidate });
  const createPage = useCallback<PageActions["createPage"]>(async (input) =>
    rowPublicId((await latest.current.create(input))?.create_page), []);
  const movePage = useCallback<PageActions["movePage"]>(async (id, parent) => {
    await latest.current.update({ id, values: { parent } });
    latest.current.invalidate([PAGE_MODEL]);
  }, []);
  return { busy: createState.fetching || update.mutation.isPending, createPage, movePage };
}
