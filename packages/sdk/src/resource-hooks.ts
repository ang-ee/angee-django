import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation as useUrqlMutation } from "urql";

import { DISABLED_DOCUMENTS } from "./disabled-documents";
import { useDocumentQuery } from "./document-query";
import {
  useInvalidateModels,
  useRegisterModelRefetch,
} from "./relay-invalidation";
import { useStableArray } from "./stable-deps";
import {
  extractDeletePreview,
  extractNode,
  extractPage,
  type DeletePreview,
  type PageInfo,
  type Row,
} from "./resource-result";
import {
  assembleDetailDocument,
  assembleListDocument,
  assembleMutationDocument,
  clampPageSize,
  DEFAULT_PAGE_SIZE,
  type MutationAction,
} from "./selection";
import type {
  ResourceFilter,
  ResourceOrder,
  ResourceTypeName,
} from "./__generated__/resource-types";

export type { PageInfo } from "./resource-result";
export type { DeletePreview, DeletePreviewGroup, DeletePreviewNode } from "./resource-result";
export type { MutationAction } from "./selection";

/** A filter accepted as the model's generated input or any record. */
type Filter<TName extends ResourceTypeName> = ResourceFilter<TName> | Record<string, unknown>;
/** A single `@oneOf` order accepted as the model's generated input or any record. */
type Order<TName extends ResourceTypeName> = ResourceOrder<TName> | Record<string, unknown>;

export interface UseResourceListOptions<TName extends ResourceTypeName> {
  fields: readonly string[];
  pageSize?: number;
  /** 1-based page owned by the caller. Use this for URL/router-owned lists. */
  page?: number;
  /** 1-based initial page; the hook then owns the page through its setters. */
  initialPage?: number;
  filter?: Filter<TName>;
  order?: Order<TName>;
  enabled?: boolean;
}

export interface UseResourceListResult {
  rows: readonly Row[];
  /** Total matching rows, owned and reported by the backend. */
  total: number | undefined;
  /** Total pages = ceil(total / pageSize); undefined until `total` is known. */
  pageCount: number | undefined;
  /** 1-based index of the page currently shown. */
  page: number;
  pageSize: number;
  pageInfo: PageInfo | undefined;
  hasNext: boolean;
  hasPrev: boolean;
  /** Jump to any 1-based page (offset pagination); clamped to `[1, pageCount]`. */
  setPage: (page: number) => void;
  firstPage: () => void;
  nextPage: () => void;
  prevPage: () => void;
  lastPage: () => void;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

/** Read an offset-paginated list of records, selecting exactly `fields`. */
export function useResourceList<TName extends ResourceTypeName = ResourceTypeName>(
  modelLabel: string,
  options: UseResourceListOptions<TName>,
): UseResourceListResult {
  const {
    fields,
    pageSize = DEFAULT_PAGE_SIZE,
    initialPage = 1,
    filter,
    order,
    enabled = true,
  } = options;
  const active = enabled && Boolean(modelLabel);
  const size = clampPageSize(pageSize);
  const stableFields = useStableArray(fields);
  const withFilter = filter !== undefined;
  const withOrder = order !== undefined;

  const document = useMemo(
    () => assembleListDocument(modelLabel, stableFields, { withFilter, withOrder }),
    [modelLabel, stableFields, withFilter, withOrder],
  );

  const filterKey = JSON.stringify(filter ?? null);
  const orderKey = JSON.stringify(order ?? null);

  const controlledPage = options.page === undefined
    ? undefined
    : normalisePage(options.page);
  const initial = normalisePage(initialPage);
  const resetKey = `${modelLabel}|${size}|${filterKey}|${orderKey}`;
  const [pageState, setPageState] = useState(() => ({
    resetKey,
    initial,
    page: initial,
  }));
  const currentPage = controlledPage
    ?? (pageState.resetKey !== resetKey
      ? 1
      : pageState.initial !== initial
        ? initial
        : pageState.page);

  useEffect(() => {
    if (controlledPage !== undefined) return;
    setPageState((current) => {
      if (current.resetKey !== resetKey) {
        return { resetKey, initial, page: 1 };
      }
      if (current.initial !== initial) {
        return { resetKey, initial, page: initial };
      }
      return current;
    });
  }, [controlledPage, initial, resetKey]);

  const variables = useMemo(() => {
    const vars: Record<string, unknown> = {
      pagination: { offset: (currentPage - 1) * size, limit: size },
    };
    if (withFilter) vars.filters = filter;
    if (withOrder) vars.order = order;
    return vars;
    // `filter`/`order` are keyed by their serialized form so the memo is stable
    // when a caller passes a fresh-but-equal object each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPage, size, withFilter, withOrder, filterKey, orderKey]);

  const run = useDocumentQuery(document, variables, active);
  // Register so a change event (and post-write invalidation) refresh this list —
  // the writes the normalized cache can't see on its own.
  useRegisterModelRefetch(modelLabel, run.refetch, active);
  // Stabilize the extracted page by the source data identity: `extractPage` allocates
  // a fresh `rows` array each call, so without this every render hands consumers a new
  // reference — which (e.g.) makes TanStack Table re-process its data and churns derived
  // memos. urql keeps `data` referentially stable until a new result arrives.
  const { rows, total, pageInfo } = useMemo(() => extractPage(run.data), [run.data]);
  const pageCount = total === undefined ? undefined : Math.max(1, Math.ceil(total / size));

  const setPage = useCallback(
    (next: number) => {
      const floored = Math.max(1, Math.floor(next));
      setPageState({
        resetKey,
        initial,
        page: pageCount ? Math.min(floored, pageCount) : floored,
      });
    },
    [initial, pageCount, resetKey],
  );
  const firstPage = useCallback(
    () => setPageState({ resetKey, initial, page: 1 }),
    [initial, resetKey],
  );
  const nextPage = useCallback(
    () =>
      setPageState((current) => {
        const page = current.resetKey === resetKey ? current.page : 1;
        return {
          resetKey,
          initial,
          page: pageCount ? Math.min(page + 1, pageCount) : page + 1,
        };
      }),
    [initial, pageCount, resetKey],
  );
  const prevPage = useCallback(
    () =>
      setPageState((current) => {
        const page = current.resetKey === resetKey ? current.page : 1;
        return { resetKey, initial, page: Math.max(1, page - 1) };
      }),
    [initial, resetKey],
  );
  const lastPage = useCallback(() => {
    if (pageCount) setPageState({ resetKey, initial, page: pageCount });
  }, [initial, pageCount, resetKey]);

  return {
    rows,
    total,
    pageCount,
    page: currentPage,
    pageSize: size,
    pageInfo,
    hasNext: pageCount !== undefined && currentPage < pageCount,
    hasPrev: currentPage > 1,
    setPage,
    firstPage,
    nextPage,
    prevPage,
    lastPage,
    fetching: run.fetching,
    error: run.error,
    refetch: run.refetch,
  };
}

function normalisePage(page: number): number {
  return Math.max(1, Math.floor(page));
}

export interface UseResourceRecordResult {
  record: Row | null;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

/** Read a single record by id, selecting exactly `fields`. */
export function useResourceRecord(
  modelLabel: string,
  id: string | null | undefined,
  options: { fields: readonly string[]; enabled?: boolean },
): UseResourceRecordResult {
  const { fields, enabled = true } = options;
  const active = enabled && Boolean(modelLabel) && Boolean(id);
  const stableFields = useStableArray(fields);

  const document = useMemo(
    () => assembleDetailDocument(modelLabel, stableFields),
    [modelLabel, stableFields],
  );
  const variables = useMemo(() => ({ id: id ?? "" }), [id]);

  const run = useDocumentQuery(document, variables, active);
  useRegisterModelRefetch(modelLabel, run.refetch, active);
  return {
    record: extractNode(run.data),
    fetching: run.fetching,
    error: run.error,
    refetch: run.refetch,
  };
}

export interface ResourceMutationVariables {
  /** For `create`/`update`: the input/patch (an `update` patch carries its id). */
  data?: Record<string, unknown>;
  /** For `delete`: the relay id to remove. */
  id?: string;
  /** For `delete`: false previews the cascade without deleting. */
  confirm?: boolean;
}

export type ResourceMutationResult<TAction extends MutationAction = MutationAction> =
  TAction extends "delete" ? DeletePreview | null : Row | null;

export type ResourceMutate<TAction extends MutationAction = MutationAction> = (
  variables: ResourceMutationVariables,
) => Promise<ResourceMutationResult<TAction>>;

/**
 * Build a create / update / delete mutation. `create`/`update` resolve to the
 * mutated node; `delete` resolves to the cascade `DeletePreview`.
 */
export function useResourceMutation<TAction extends MutationAction>(
  modelLabel: string,
  action: TAction,
  options: { fields?: readonly string[] } = {},
): [ResourceMutate<TAction>, { fetching: boolean; error: Error | null }] {
  const fields = options.fields ?? [];
  const fieldsKey = fields.join(" ");
  const document = useMemo(
    () =>
      modelLabel
        ? assembleMutationDocument(modelLabel, action, fields)
        : DISABLED_DOCUMENTS.mutation,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [modelLabel, action, fieldsKey],
  );

  const [state, execute] = useUrqlMutation(document);
  const invalidateModels = useInvalidateModels();
  const mutate = useCallback<ResourceMutate<TAction>>(
    async (variables) => {
      const result = await execute(variables);
      if (result.error) throw result.error;
      // create/delete change list membership the normalized cache can't infer;
      // update returns the same entity, so graphcache refreshes it in place.
      if (action === "create" || (action === "delete" && variables.confirm !== false)) {
        invalidateModels([modelLabel]);
      }
      return (
        action === "delete"
          ? extractDeletePreview(result.data)
          : extractNode(result.data)
      ) as ResourceMutationResult<TAction>;
    },
    [execute, invalidateModels, action, modelLabel],
  );

  return [mutate, { fetching: state.fetching, error: state.error ?? null }];
}
