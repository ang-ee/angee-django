import { useCallback, useMemo, useRef, useState } from "react";
import { useMutation as useUrqlMutation } from "urql";

import { DISABLED_DOCUMENTS } from "./disabled-documents";
import { useDocumentQuery } from "./document-query";
import {
  useInvalidateModels,
  useRegisterModelRefetch,
} from "./relay-invalidation";
import { useStableArray } from "./stable-deps";
import {
  extractNode,
  extractPage,
  type PageInfo,
  type Row,
} from "./resource-result";
import {
  assembleDetailDocument,
  assembleListDocument,
  assembleMutationDocument,
  clampPageSize,
  type MutationAction,
} from "./selection";
import type {
  ResourceFilter,
  ResourceOrder,
  ResourceTypeName,
} from "./__generated__/resource-types";

export type { PageInfo } from "./resource-result";
export type { MutationAction } from "./selection";

/** A filter accepted as the model's generated input or any record. */
type Filter<TName extends ResourceTypeName> = ResourceFilter<TName> | Record<string, unknown>;
/** A single `@oneOf` order accepted as the model's generated input or any record. */
type Order<TName extends ResourceTypeName> = ResourceOrder<TName> | Record<string, unknown>;

export interface UseResourceListOptions<TName extends ResourceTypeName> {
  fields: readonly string[];
  pageSize?: number;
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

const DEFAULT_PAGE_SIZE = 50;

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

  const [page, setPageState] = useState(() => Math.max(1, Math.floor(initialPage)));

  // A new query identity (model, page size, filter, or order) resets to page 1.
  // Adjust during render — not in an effect — so the first request against the
  // new query uses the reset offset, never a stale deep-page offset.
  const resetKey = `${modelLabel}|${size}|${filterKey}|${orderKey}`;
  const resetKeyRef = useRef(resetKey);
  let currentPage = page;
  if (resetKeyRef.current !== resetKey) {
    resetKeyRef.current = resetKey;
    currentPage = 1;
    setPageState(1);
  }

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
  const { rows, total, pageInfo } = extractPage(run.data);
  const pageCount = total === undefined ? undefined : Math.max(1, Math.ceil(total / size));

  const setPage = useCallback(
    (next: number) => {
      const floored = Math.max(1, Math.floor(next));
      setPageState(pageCount ? Math.min(floored, pageCount) : floored);
    },
    [pageCount],
  );
  const firstPage = useCallback(() => setPageState(1), []);
  const nextPage = useCallback(
    () => setPageState((current) => (pageCount ? Math.min(current + 1, pageCount) : current + 1)),
    [pageCount],
  );
  const prevPage = useCallback(() => setPageState((current) => Math.max(1, current - 1)), []);
  const lastPage = useCallback(() => {
    if (pageCount) setPageState(pageCount);
  }, [pageCount]);

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
}

export type ResourceMutate = (
  variables: ResourceMutationVariables,
) => Promise<Row | null>;

/**
 * Build a create / update / delete mutation. `create`/`update` resolve to the
 * mutated node; `delete` resolves to the cascade `DeletePreview`.
 */
export function useResourceMutation(
  modelLabel: string,
  action: MutationAction,
  options: { fields?: readonly string[] } = {},
): [ResourceMutate, { fetching: boolean; error: Error | null }] {
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
  const mutate = useCallback<ResourceMutate>(
    async (variables) => {
      const result = await execute(variables);
      if (result.error) throw result.error;
      // create/delete change list membership the normalized cache can't infer;
      // update returns the same entity, so graphcache refreshes it in place.
      if (action === "create" || action === "delete") {
        invalidateModels([modelLabel]);
      }
      return extractNode(result.data);
    },
    [execute, invalidateModels, action, modelLabel],
  );

  return [mutate, { fetching: state.fetching, error: state.error ?? null }];
}
