import { useCallback, useMemo } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import {
  useCustomMutation,
  useDataProvider,
  useInvalidate,
  useKeys,
  useResourceSubscription,
  type BaseRecord,
  type HttpError,
} from "@refinedev/core";

import { listQueryMeta, type ListQueryTarget } from "../list-query";
import {
  aggregateRequest,
  actionRequest,
  deletePreviewRequest,
  extractActionOutcome,
  extractAggregate,
  extractDeletePreview,
  extractFacet,
  extractGroupBy,
  extractSaveResult,
  groupByRequest,
  saveRequest,
  type ActionOutcome,
  type AggregateBucket,
  type AggregateRequestOptions,
  type CustomGraphQLOperationTarget,
  type DeletePreview,
  type DeletePreviewVariables,
  type FacetRequestSpec,
  type GroupByRequestOptions,
  type GroupByResult,
  type ResourceFacetResult,
  type ResourceSaveVariables,
} from "../operations";
import {
  maybeOperationDocument,
  operationDocument,
  useOperationDocuments,
} from "../operation-documents";
import { useActiveDataProviderName } from "./data-provider-context";
import {
  useAuthoredMutation,
  useAuthoredQueryBatch,
  type AuthoredMutateOptions,
  type AuthoredMutationOptions,
  type AuthoredQueryOptions,
} from "./authored-hooks";
import type { TypedDocumentNode } from "../typed-document";
import { graphqlDocumentIdentity } from "./wire";
import { labelBatchResults, projectQueryBatch, useDerivedBatch, useReportRefusedReads, type QueryBatchEntry } from "./query-batch";
import { stableKey } from "../stable-deps";

type Row = Record<string, unknown>;
type InvalidateParams = Parameters<ReturnType<typeof useInvalidate>>[0];

export interface ListBatchTarget extends ListQueryTarget {
  dataProviderName: string | undefined;
  resourceIdentifier: string;
  resourceName: string;
}

export interface DialectDocumentOptions {
  document: unknown;
}

type DialectQueryOptions = DialectDocumentOptions
  & Pick<AuthoredQueryOptions, "enabled" | "refetchInterval" | "relatedModels">;

export interface UseAngeeAggregateResult {
  aggregate: AggregateBucket | null;
  fetching: boolean;
  error: Error | null;
  /** Timestamp of the most recent successful native query read. */
  updatedAt: number | null;
  refetch: () => void;
}

export interface UseAngeeGroupByResult extends GroupByResult {
  fetching: boolean;
  error: HttpError | Error | null;
  /** Timestamp of the most recent successful native query read. */
  updatedAt: number | null;
  refetch: () => void;
}

const EMPTY_GROUP_BY_RESULT: GroupByResult = {
  count: 0,
  totalCount: 0,
  buckets: [],
};
const EMPTY_FACET_RESULT: ResourceFacetResult = { count: 0, totalCount: 0, options: [] };

export interface UseAngeeFacetsOptions {
  enabled?: boolean;
  facets: readonly FacetRequestSpec[];
}

/** One `_groups` request in a batch, addressed by a caller-stable `key`. */
export interface GroupByBatchScope {
  key: string;
  query: GroupByRequestOptions;
}

/** One leaf `list` request in a batch, addressed by a caller-stable `key`. */
export interface AngeeListBatchScope {
  key: string;
  where: Record<string, unknown> | undefined;
  orderBy: unknown;
  page: number;
  pageSize: number;
}

export interface AngeeListBatchEntry {
  /** Refetch this active native record page. */
  refetch: () => void;
  rows: readonly Row[];
  total: number | undefined;
  fetching: boolean;
  error: Error | null;
  /** Timestamp of the most recent successful native query read; null until the page first settles. */
  updatedAt: number | null;
}

export interface UseAngeeFacetsResult {
  facets: Readonly<Record<string, ResourceFacetResult>>;
  /** Each facet's own read's error, by facet id: a refused facet says why while the others keep counting. */
  errors: Readonly<Record<string, Error | null>>;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

export interface UseAngeeDeletePreviewResult {
  preview: DeletePreview | null;
  fetching: boolean;
  error: HttpError | null;
  /** Native Refine notification opt-out when the invoking surface owns feedback. */
  mutate: (variables: DeletePreviewVariables, options?: { errorNotification?: false }) => Promise<DeletePreview | null>;
  reset: () => void;
}

export interface UseAngeeResourceSaveResult {
  save: (variables: ResourceSaveVariables) => Promise<Row | null>;
  fetching: boolean;
  error: HttpError | null;
  reset: () => void;
}

/**
 * Fire a derived action with its declared record-id binding and required arguments,
 * and resolve its in-band `ActionOutcome` (`undefined` when the transport
 * returned no payload). A domain failure resolves as
 * `ok=false` rather than throwing — project through `runActionResult` where the
 * caller wants the rendered action's throw-on-failure/success-message contract.
 */
export type ActionArguments = Readonly<Record<string, unknown>>;

export type ActionMutate = (
  id: string,
  arguments_?: ActionArguments,
  options?: AuthoredMutateOptions,
) => Promise<ActionOutcome | undefined>;

export interface UseActionMutationOptions extends Pick<AuthoredMutationOptions, "transient" | "errorNotification"> {
  dataProviderName?: string;
  /** Declared record argument. Defaults to `id`; null sends only explicit arguments. */
  idArgument?: string | null;
  /**
   * Exact canonical Angee model labels whose authored reads this action moves.
   * This metadata-free package exact-matches them and does not resolve aliases.
   */
  invalidateModels?: readonly string[];
  /** Refine invalidation calls this action should trigger after success. */
  invalidates?: readonly InvalidateParams[];
  /**
   * The action records its outcome even when it fails (`ok=false`) — a failed
   * provision stores the error it hit — so invalidate after a domain failure too.
   */
  invalidateOnFailure?: boolean;
}

export interface UseActionMutationState {
  fetching: boolean;
  error: Error | null;
}

const EMPTY_FACETS: readonly FacetRequestSpec[] = [];
const EMPTY_GROUP_BY_SCOPES: readonly GroupByBatchScope[] = [];
const EMPTY_LIST_SCOPES: readonly AngeeListBatchScope[] = [];

/** Collection reads include records that can enter their filter from outside the current page. */
function collectionLiveInterest(model: string | undefined, relatedModels: readonly string[] = []) {
  return {
    models: model ? [model, ...relatedModels] : [],
    records: model && relatedModels.length > 0 ? [{ model }] : [],
    relatedModels: relatedModels.filter((related) => related !== model),
  };
}

export function useAngeeAggregate(
  target: CustomGraphQLOperationTarget | null,
  options: AggregateRequestOptions & DialectQueryOptions,
): UseAngeeAggregateResult {
  const { document, enabled = true, refetchInterval, relatedModels, ...query } = options;
  const queryKey = stableKey(query);
  const request = useMemo(
    () => (target ? aggregateRequest(target, query, { document }) : null),
    [document, target, queryKey],
  );
  const scopes = useMemo(() => request ? [{
    key: "single",
    document: graphqlDocumentIdentity(request.meta.gqlQuery).document as TypedDocumentNode<unknown, Record<string, unknown>>,
    variables: request.meta.gqlVariables as Record<string, unknown>,
    ...collectionLiveInterest(target?.modelLabel, relatedModels),
  }] : [], [request, target?.modelLabel, relatedModels]);
  const run = useAuthoredQueryBatch(scopes, {
    enabled: enabled && target !== null,
    dataProviderName: request?.dataProviderName,
    refetchInterval,
  }).get("single");
  const root = request?.root;
  // Decoded once per read, so a render that changes nothing hands back the same result.
  return useMemo(() => ({
    aggregate: root === undefined ? null : extractAggregate(run?.data, root),
    fetching: run?.isFetching ?? false,
    error: run?.error ?? null,
    updatedAt: run?.dataUpdatedAt || null,
    refetch: () => { void run?.refetch(); },
  }), [root, run]);
}

export function useAngeeGroupBy(
  target: CustomGraphQLOperationTarget | null,
  options: GroupByRequestOptions & DialectQueryOptions,
): UseAngeeGroupByResult {
  const { document, enabled = true, refetchInterval, relatedModels, ...query } = options;
  const queryKey = stableKey(query);
  const scopes = useMemo<readonly GroupByBatchScope[]>(
    () => [{ key: "single", query }],
    [queryKey],
  );
  // One read is a batch of one: decoded once per read, its refusal reported once.
  const results = useAngeeGroupByBatch(target, scopes, { document, enabled, refetchInterval, relatedModels });
  const root = target?.root;
  const absent = useMemo(() => groupByResult(undefined, root), [root]);
  return results.get("single") ?? absent;
}

/**
 * A group read's entry as its result, for one read and for a batch alike: a payload
 * the decoder refuses is that read's error, never a render that throws.
 */
function groupByResult(entry: QueryBatchEntry<unknown> | undefined, root: string | undefined): UseAngeeGroupByResult {
  let result = EMPTY_GROUP_BY_RESULT;
  let decodeError: Error | null = null;
  if (root !== undefined && entry?.data != null) {
    try {
      result = extractGroupBy(entry.data, root);
    } catch (cause) {
      decodeError = errorFromHttp(cause);
    }
  }
  return {
    ...result,
    fetching: entry?.isFetching ?? false,
    error: entry?.error ?? decodeError,
    updatedAt: entry?.dataUpdatedAt || null,
    refetch: () => { void entry?.refetch(); },
  };
}

export function useAngeeFacets(
  target: CustomGraphQLOperationTarget | null,
  options: UseAngeeFacetsOptions & DialectDocumentOptions,
): UseAngeeFacetsResult {
  const { document, enabled = true, facets } = options;
  const canQuery = enabled && target !== null && facets.length > 0;
  const activeFacets = canQuery ? facets : EMPTY_FACETS;
  const scopes = useMemo<readonly GroupByBatchScope[]>(
    () => activeFacets.map((facet) => ({ key: facet.id, query: facet })),
    [activeFacets],
  );
  const batch = useGroupByRequestBatch(target, scopes, { document, enabled: canQuery });
  const root = target?.root ?? "";
  // A facet decodes again only when its own read does: two facets may share a read.
  // A payload the decoder refuses is the facets' error, as it is a group read's.
  const decoded = useDerivedBatch(batch, (entry, id) => {
    const facet = activeFacets.find((spec) => spec.id === id);
    if (!facet || entry.data == null) return { result: EMPTY_FACET_RESULT, error: null };
    try {
      return { result: extractFacet(entry.data, root, facet), error: null };
    } catch (cause) {
      return { result: EMPTY_FACET_RESULT, error: errorFromHttp(cause) };
    }
  }, [activeFacets, root]);
  useReportRefusedReads(batch, decoded);
  return useMemo(() => {
    const values = [...new Set(batch.values())];
    return {
      facets: Object.fromEntries(
        activeFacets.map((facet) => [facet.id, decoded.get(facet.id)?.result ?? EMPTY_FACET_RESULT]),
      ),
      errors: Object.fromEntries(activeFacets.map((facet) => [
        facet.id, batch.get(facet.id)?.error ?? decoded.get(facet.id)?.error ?? null,
      ])),
      fetching: values.some((entry) => entry.isFetching),
      error: values.find((entry) => entry.error)?.error
        ?? [...decoded.values()].find((facet) => facet.error)?.error ?? null,
      refetch: () => {
        values.forEach((entry) => { void entry.refetch(); });
      },
    };
  }, [activeFacets, batch, decoded]);
}

/**
 * Batch any number of `_groups` requests through one {@link useQueries} call: the
 * dynamic-length array is a single hook, so the request set can grow over renders
 * (e.g. a grouped surface revealing deeper sub-group levels as parents resolve)
 * without breaking the rules of hooks. The shared core behind {@link useAngeeFacets}
 * and {@link useAngeeGroupByBatch}; results are addressed by each scope's `key`.
 */
function useGroupByRequestBatch(
  target: CustomGraphQLOperationTarget | null,
  scopes: readonly GroupByBatchScope[],
  options: DialectQueryOptions,
): ReadonlyMap<string, QueryBatchEntry<unknown>> {
  const { document, enabled = true, refetchInterval, relatedModels } = options;
  const canQuery = enabled && target !== null;
  const activeScopes = canQuery ? scopes : EMPTY_GROUP_BY_SCOPES;
  const scopesKey = stableKey(activeScopes);
  const requests = useMemo(() => {
    if (!target) return [];
    return activeScopes.map((scope) => {
      const request = groupByRequest(target, scope.query, { document });
      return {
        key: scope.key,
        document: graphqlDocumentIdentity(request.meta.gqlQuery).document as TypedDocumentNode<unknown, Record<string, unknown>>,
        variables: request.meta.gqlVariables as Record<string, unknown>,
        ...collectionLiveInterest(target.modelLabel, relatedModels),
      };
    });
  }, [activeScopes, document, target, scopesKey, relatedModels]);
  // The authored owner deduplicates requests and unions invalidation interests;
  // the dialect owns only group variables and the caller's result labels.
  return useAuthoredQueryBatch(requests, {
    enabled: canQuery, dataProviderName: target?.dataProviderName, refetchInterval,
  });
}

/**
 * Batch the per-level `_groups` aggregates of a server-grouped view into one
 * request round. Each scope resolves to the same {@link GroupByResult} a single
 * {@link useAngeeGroupBy} would, keyed by the scope's `key`.
 */
export function useAngeeGroupByBatch(
  target: CustomGraphQLOperationTarget | null,
  scopes: readonly GroupByBatchScope[],
  options: DialectQueryOptions,
): ReadonlyMap<string, UseAngeeGroupByResult> {
  const batch = useGroupByRequestBatch(target, scopes, options);
  const root = target?.root;
  const results = useDerivedBatch(batch, (entry) => groupByResult(entry, root), [root]);
  useReportRefusedReads(batch, results);
  return results;
}

/**
 * Batch the leaf record pages of a server-grouped view into one {@link useQueries}
 * round — one `getList` per currently-rendered expanded bucket, instead of one
 * `useList` hook mounted per bucket. The query keys mirror refine's `useList` so
 * `useInvalidate` reaches them, and a single static resource-level subscription
 * re-opens the live `changes()` feed (key parity alone never re-subscribes).
 */
export function useAngeeListBatch(
  target: ListBatchTarget | null,
  scopes: readonly AngeeListBatchScope[],
  options: { fields: readonly string[]; enabled?: boolean; relatedModels?: readonly string[] },
): ReadonlyMap<string, AngeeListBatchEntry> {
  const enabled = options.enabled ?? true;
  const canQuery = enabled && target !== null;
  const activeScopes = canQuery ? scopes : EMPTY_LIST_SCOPES;
  const { keys } = useKeys();
  const client = useQueryClient();
  const dataProvider = useDataProvider();
  const resourceName = target?.resourceName ?? "";
  const identifier = target?.resourceIdentifier ?? "";
  const schemaName = target?.dataProviderName;
  // A caller derives its target and scopes per render: their contents, not the
  // objects, key the requests. The requests, their hashes and each label's request
  // are one value, so the labels cannot outlive the requests they name. Labels on
  // one request share it, as in the authored batch.
  const targetKey = stableKey(target);
  const fieldsKey = stableKey(options.fields);
  const scopesKey = stableKey(activeScopes);
  const batch = useMemo(() => {
    const labelled = activeScopes.map((scope) => {
      const meta = listQueryMeta(target!, options.fields, scope.where, scope.orderBy);
      const pagination = { mode: "server" as const, currentPage: scope.page, pageSize: scope.pageSize };
      const queryKey = keys().data(schemaName).resource(identifier).action("list")
        .params({ ...meta, filters: [], pagination, sorters: [] }).get();
      return { label: scope.key, meta, pagination, queryKey, hash: client.defaultQueryOptions({ queryKey }).queryHash };
    });
    const requests = new Map(labelled.map((request) => [request.hash, request]));
    return {
      requests: [...requests.values()],
      hashes: [...requests.keys()],
      labels: labelled.map(({ label, hash }) => [label, hash] as const),
    };
  }, [client, scopesKey, targetKey, fieldsKey]);
  // One static, resource-level live subscription re-opens the websocket changes()
  // feed for these rows: refine's auto liveMode invalidates the resource list cache
  // on each change, and the keys below are the very keys useList builds, so the
  // invalidation reaches them. (Matching query keys alone never re-subscribes.)
  useResourceSubscription({
    channel: `resources/${resourceName}`,
    resource: resourceName,
    types: ["*"],
    params: { subscriptionType: "useList", relatedModels: options.relatedModels },
    enabled: canQuery,
    meta: { dataProviderName: schemaName },
  });
  const entries = useQueries({
    combine: (current) => projectQueryBatch(batch.hashes, current),
    queries: batch.requests.map(({ meta, pagination, queryKey }) => ({
      queryKey,
      queryFn: () =>
        dataProvider(schemaName).getList({
          resource: resourceName,
          pagination,
          filters: [],
          sorters: [],
          meta,
        }),
      enabled: canQuery,
    })),
  });
  // Each request's page, derived once and read by every label on it.
  const byRequest = useMemo(() => new Map(Object.entries(entries)), [entries]);
  const pages = useDerivedBatch(byRequest, (query) => ({
    refetch: () => { void query.refetch(); },
    rows: (query.data?.data ?? []) as readonly Row[],
    total: query.data?.total,
    fetching: query.isFetching,
    error: errorFromHttp(query.error),
    updatedAt: query.dataUpdatedAt || null,
  }), []);
  return useMemo(() => labelBatchResults("useAngeeListBatch", batch.labels, Object.fromEntries(pages)), [batch, pages]);
}

export function useAngeeDeletePreview(
  target: CustomGraphQLOperationTarget | null,
  options: DialectDocumentOptions,
): UseAngeeDeletePreviewResult {
  const { document } = options;
  const root = target?.root ?? "";
  const run = useCustomMutation<BaseRecord, HttpError, DeletePreviewVariables>();
  const mutate = useCallback(
    async (variables: DeletePreviewVariables, options?: { errorNotification?: false }) => {
      if (!target) return null;
      const request = deletePreviewRequest(target, variables, { document });
      const response = await run.mutateAsync({
        url: "",
        method: "post",
        values: variables,
        ...options,
        dataProviderName: request.dataProviderName,
        meta: request.meta,
      });
      return extractDeletePreview(response.data, request.root);
    },
    [document, target, run.mutateAsync],
  );
  return {
    preview: root ? extractDeletePreview(run.mutation.data?.data, root) : null,
    fetching: run.mutation.isPending,
    error: run.mutation.error,
    mutate,
    reset: run.mutation.reset,
  };
}

/**
 * Run the authored `<resource>_save(pk, patch, lines)` diff-apply mutation (F6)
 * through refine's custom mutation owner — the transactional parent-patch-plus-line
 * upsert/delete write. Mirrors `useAngeeDeletePreview`: the metadata edge resolves the
 * `save` root as `target`, and the caller passes the generated `document` for that
 * root (the single backend-binding point, see `saveRequest`). Returns the saved parent
 * row (with its returned lines) so the form re-seeds from the server truth.
 */
export function useAngeeResourceSave(
  target: CustomGraphQLOperationTarget | null,
  options: DialectDocumentOptions,
): UseAngeeResourceSaveResult {
  const { document } = options;
  const run = useCustomMutation<BaseRecord, HttpError, ResourceSaveVariables>();
  const save = useCallback(
    async (variables: ResourceSaveVariables) => {
      if (!target) return null;
      const request = saveRequest(target, variables, { document });
      const response = await run.mutateAsync({
        url: "",
        method: "post",
        values: variables,
        dataProviderName: request.dataProviderName,
        meta: request.meta,
      });
      return extractSaveResult(response.data, request.root);
    },
    [document, target, run.mutateAsync],
  );
  return {
    save,
    fetching: run.mutation.isPending,
    error: run.mutation.error,
    reset: run.mutation.reset,
  };
}

/**
 * Run a derived backend action mutation through refine's custom mutation
 * owner. The generated per-schema `ActionFieldName` union still pins callers to
 * real action fields, while refine owns execution state.
 * The mutate resolves the in-band `ActionOutcome` (`ok`, `message`, and the
 * created record's `id` when the verb returns one) so callers settle success,
 * failure, and deep-linking from one value; registered resource invalidations
 * and authored-read model invalidations run only when the outcome is not a
 * domain failure, unless `invalidateOnFailure` declares that the verb's
 * failures persist state too. A call's `AuthoredMutateOptions` turn refine's
 * generic error notification off for a surface that renders the failure itself
 * (`MutationDialog`).
 */
export function useActionMutation<TField extends string = string>(
  field: TField,
  options: UseActionMutationOptions = {},
): [ActionMutate, UseActionMutationState] {
  // Same fallback chain the authored hooks resolve: an explicit option, then the
  // ambient DataProviderContext (a resource page's schema, e.g. "console"), then
  // the "default" alias. Resolving the active schema keeps the action-document
  // lookup below (keyed by real schema name) from asking for a "default" key that
  // `operationDocumentsForSchemas` never aliases — the Post-button asymmetry.
  const activeDataProviderName = useActiveDataProviderName();
  const dataProviderName =
    options.dataProviderName ?? activeDataProviderName ?? "default";
  const operationDocuments = useOperationDocuments();
  // Generated registry documents carry this action envelope. Keep missing-document
  // validation at dispatch, so a not-yet-visible action need not have a document.
  const document = maybeOperationDocument(operationDocuments, dataProviderName, "actions", field) as
    TypedDocumentNode<Record<string, unknown>, Record<string, unknown>>;
  const [run, state] = useAuthoredMutation(document, {
    ...options,
    dataProviderName,
    shouldInvalidate: (data) => {
      const outcome = extractActionOutcome(data, field);
      return !outcome || outcome.ok || options.invalidateOnFailure === true;
    },
  });
  const mutate = useCallback<ActionMutate>(
    async (id, arguments_ = {}, callOptions) => {
      const idArgument = options.idArgument === undefined ? "id" : options.idArgument;
      const variables = idArgument === null ? { ...arguments_ } : { ...arguments_, [idArgument]: id };
      const request = actionRequest(field, variables, {
        dataProviderName,
        document: operationDocument(
          operationDocuments,
          dataProviderName,
          "actions",
          field,
        ),
      });
      return extractActionOutcome(await run(variables, callOptions), request.root) ?? undefined;
    },
    [
      dataProviderName,
      field,
      operationDocuments,
      options.idArgument,
      run,
    ],
  );
  return [mutate, state];
}

function errorFromHttp(error: unknown): Error | null {
  if (!error) return null;
  if (error instanceof Error) return error;
  const message =
    typeof error === "object" && error !== null && "message" in error
      ? String((error as { message?: unknown }).message)
      : String(error);
  return new Error(message);
}
