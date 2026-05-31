import { useCallback, useMemo } from "react";
import { useMutation as useUrqlMutation } from "urql";

import { DISABLED_DOCUMENTS } from "./disabled-documents";
import { useDocumentQuery } from "./document-query";
import {
  extractConnection,
  extractNode,
  type PageInfo,
  type Row,
} from "./resource-result";
import {
  assembleDetailDocument,
  assembleListDocument,
  assembleMutationDocument,
  pageToConnectionArgs,
  type MutationAction,
} from "./selection";
import type {
  ResourceFilter,
  ResourceOrder,
  ResourceTypeName,
} from "./__generated__/resource-types";

export type { PageInfo } from "./resource-result";

/** A filter/order accepted as the model's generated input or any record. */
type Filter<TName extends ResourceTypeName> = ResourceFilter<TName> | Record<string, unknown>;
type Order<TName extends ResourceTypeName> =
  | ResourceOrder<TName>
  | Record<string, unknown>
  | ReadonlyArray<ResourceOrder<TName> | Record<string, unknown>>;

export interface UseResourceListOptions<TName extends ResourceTypeName> {
  fields: readonly string[];
  page?: number;
  pageSize?: number;
  search?: string;
  filter?: Filter<TName>;
  order?: Order<TName>;
  enabled?: boolean;
}

export interface UseResourceListResult {
  rows: readonly Row[];
  total: number | undefined;
  pageInfo: PageInfo | undefined;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

const DEFAULT_PAGE_SIZE = 50;

/** Read a relay-paginated list of records, selecting exactly `fields`. */
export function useResourceList<TName extends ResourceTypeName = ResourceTypeName>(
  modelLabel: string,
  options: UseResourceListOptions<TName>,
): UseResourceListResult {
  const {
    fields,
    page = 1,
    pageSize = DEFAULT_PAGE_SIZE,
    search,
    filter,
    order,
    enabled = true,
  } = options;
  const active = enabled && Boolean(modelLabel);
  const fieldsKey = fields.join(" ");
  const withFilter = filter !== undefined;
  const withOrder = order !== undefined;

  const document = useMemo(
    () => assembleListDocument(modelLabel, fields, { withFilter, withOrder }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [modelLabel, fieldsKey, withFilter, withOrder],
  );

  const variables = useMemo(() => {
    const { first, after } = pageToConnectionArgs(page, pageSize);
    const vars: Record<string, unknown> = { first, after, search: search ?? null };
    if (withFilter) vars.filters = filter;
    if (withOrder) vars.order = Array.isArray(order) ? order : [order];
    return vars;
  }, [page, pageSize, search, withFilter, withOrder, filter, order]);

  const run = useDocumentQuery(document, variables, active);
  const { rows, total, pageInfo } = extractConnection(run.data);
  return { rows, total, pageInfo, fetching: run.fetching, error: run.error, refetch: run.refetch };
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
  const fieldsKey = fields.join(" ");

  const document = useMemo(
    () => assembleDetailDocument(modelLabel, fields),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [modelLabel, fieldsKey],
  );
  const variables = useMemo(() => ({ id: id ?? "" }), [id]);

  const run = useDocumentQuery(document, variables, active);
  return {
    record: extractNode(run.data),
    fetching: run.fetching,
    error: run.error,
    refetch: run.refetch,
  };
}

export interface ResourceMutationVariables {
  id?: string;
  input?: Record<string, unknown>;
}

export type ResourceMutate = (
  variables: ResourceMutationVariables,
) => Promise<Row | null>;

/** Build a create / update / delete mutation, returning the mutated node. */
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
  const mutate = useCallback<ResourceMutate>(
    async (variables) => {
      const result = await execute(variables);
      if (result.error) throw result.error;
      return extractNode(result.data);
    },
    [execute],
  );

  return [mutate, { fetching: state.fetching, error: state.error ?? null }];
}
