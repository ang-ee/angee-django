import { useEffect, useRef } from "react";
import { print } from "graphql";
import {
  useDataProvider,
  useOnError,
  useHandleNotification,
  useTranslate,
  type BaseRecord,
} from "@refinedev/core";
import {
  infiniteQueryOptions,
  queryOptions,
  useQueryClient,
  type DataTag,
  type InfiniteData,
  type Query,
  type QueryClient,
  type QueryFunctionContext,
  type QueryKey,
  type UnusedSkipTokenOptions,
} from "@tanstack/react-query";

import type { DocumentData } from "../typed-document";
import type { AuthoredDocument, AuthoredVariables } from "./authored-hooks";
import { authoredOperationData, queryMeta } from "./wire";

/** One cache identity for finite singleton, batch and imperative authored reads. */
export function authoredQueryKey<TDocument extends AuthoredDocument>(
  document: TDocument,
  variables: AuthoredVariables<TDocument> | undefined,
  dataProviderName = "default",
) {
  return [
    "angee", "authored", "finite", dataProviderName,
    print(document), variables ?? {},
  ] as const;
}

type DataProviderGetter = ReturnType<typeof useDataProvider>;

/** The provider owns the wire request; Query owns execution and cancellation. */
export async function requestAuthoredData<TData>(
  dataProvider: DataProviderGetter,
  dataProviderName: string,
  document: AuthoredDocument,
  variables: Record<string, unknown>,
  context: QueryFunctionContext,
): Promise<TData> {
  const custom = dataProvider(dataProviderName).custom;
  if (!custom) {
    throw new Error(
      `Data provider "${dataProviderName}" does not support custom authored queries.`,
    );
  }
  const response = await custom<BaseRecord>({
    url: "",
    method: "post",
    // Refine exposes Query context in request metadata. Hasura7 ignores the
    // signal; consuming it here still lets Query discard a cancelled result.
    meta: {
      ...queryMeta(document, variables),
      queryKey: context.queryKey,
      signal: context.signal,
    },
  });
  const data = authoredOperationData<TData>(response.data);
  if (data === undefined) throw new Error("Authored query returned no data.");
  return data;
}

/** Share options without wrapping the native result or duplicating its data. */
export function authoredQueryOptions<TDocument extends AuthoredDocument>(
  client: QueryClient,
  dataProvider: DataProviderGetter,
  dataProviderName: string,
  document: TDocument,
  variables?: AuthoredVariables<TDocument>,
  models: readonly string[] = [],
): UnusedSkipTokenOptions<
  DocumentData<TDocument>, Error, DocumentData<TDocument>,
  ReturnType<typeof authoredQueryKey<TDocument>>
> & {
  queryKey: DataTag<ReturnType<typeof authoredQueryKey<TDocument>>, DocumentData<TDocument>, Error>;
} {
  // Preserve the native named return type in declarations, including DataTag's
  // unique symbols; expanding inferred options emits unbound symbol references.
  const queryKey = authoredQueryKey(document, variables, dataProviderName);
  return queryOptions({
    queryKey,
    meta: sharedAuthoredMeta(client, queryKey, models),
    queryFn: (context) => requestAuthoredData<DocumentData<TDocument>>(
      dataProvider,
      dataProviderName,
      document,
      variables ?? {},
      context,
    ),
    placeholderData: undefined,
  });
}

/**
 * A cache entry retains every declared interest until native Query GC removes it.
 * All observers share this metadata object, so a later observer cannot replace
 * an earlier observer's interests. These are canonical labels, never aliases.
 */
export function sharedAuthoredMeta(
  client: QueryClient,
  queryKey: QueryKey,
  models: readonly string[],
) {
  const defaulted = client.defaultQueryOptions({ queryKey });
  const query = client.getQueryCache().build(client, defaulted);
  // Host defaults may share a single meta object across every query. This entry
  // needs its own object before observers can union interests in place.
  const meta = !query.meta || query.meta === defaulted.meta
    ? { ...query.meta }
    : query.meta;
  const previous = Array.isArray(meta.angeeModels)
    ? meta.angeeModels as string[]
    : [];
  meta.angeeModels = [...new Set([...previous, ...models])].sort();
  if (query.meta !== meta) query.setOptions({ ...query.options, meta });
  return meta;
}

// The native Query owns the error generation and lifetime. A shared failed
// request triggers Refine auth/notification policy once across all its observers.
const handledQueryErrors = new WeakMap<Query, {
  errorUpdateCount: number;
  errorUpdatedAt: number;
  action?: object;
}>();

export function useAuthoredErrorPolicy(keys: readonly QueryKey[]): void {
  const client = useQueryClient();
  const { mutate: checkError } = useOnError();
  const notify = useHandleNotification();
  const translate = useTranslate();
  const callbacks = useRef({ checkError, notify, translate });
  callbacks.current = { checkError, notify, translate };
  const keySignature = JSON.stringify(
    [...new Set(keys.map((queryKey) =>
      client.defaultQueryOptions({ queryKey }).queryHash,
    ))].sort(),
  );
  useEffect(() => {
    const hashes = new Set<string>(JSON.parse(keySignature));
    const report = (query: Query, action?: object) => {
      if (
        !hashes.has(query.queryHash)
        || query.state.status !== "error"
        || !query.state.error
      ) return;
      const previous = handledQueryErrors.get(query);
      const { errorUpdateCount, errorUpdatedAt } = query.state;
      // Event actions identify each native failure even after resetQueries resets
      // counters. Mounting a second observer of that same failure must not replay it.
      if (
        action
          ? previous?.action === action
          : previous?.errorUpdateCount === errorUpdateCount
            && previous?.errorUpdatedAt === errorUpdatedAt
      ) return;
      handledQueryErrors.set(query, { errorUpdateCount, errorUpdatedAt, action });
      const error = query.state.error as Error & { statusCode?: number };
      const { checkError, notify, translate } = callbacks.current;
      checkError(error);
      notify(undefined, {
        key: "post-notification",
        message: translate(
          "notifications.error",
          { statusCode: error.statusCode },
          `Error (status code: ${error.statusCode})`,
        ),
        description: error.message,
        type: "error",
      });
    };
    // Subscribe to the native owner instead of relying on observed result props:
    // a data-only consumer does not rerender when a fetch fails with unchanged data.
    const cache = client.getQueryCache();
    const unsubscribe = cache.subscribe((event) => {
      if (event.type === "updated" && event.action.type === "error") {
        report(event.query, event.action);
      }
    });
    cache.getAll().forEach((query) => report(query));
    return unsubscribe;
  }, [client, keySignature]);
}

/** Infinite data has a distinct native cache shape and namespace. */
export function authoredInfiniteQueryOptions<
  TDocument extends AuthoredDocument,
  TPage extends Partial<AuthoredVariables<TDocument>>,
>(
  client: QueryClient,
  dataProvider: DataProviderGetter,
  dataProviderName: string,
  document: TDocument,
  variables: AuthoredVariables<TDocument>,
  getNextPageParam: (
    lastPage: DocumentData<TDocument>,
    pages: DocumentData<TDocument>[],
  ) => TPage | undefined,
  models: readonly string[] = [],
): ReturnType<typeof infiniteQueryOptions<
  DocumentData<TDocument>, Error, InfiniteData<DocumentData<TDocument>, TPage | null>,
  QueryKey, TPage | null
>> {
  type Data = DocumentData<TDocument>;
  type PageParam = TPage | null;
  const queryKey = [
    "angee", "authored", "infinite", dataProviderName,
    print(document), variables,
  ] as const;
  return infiniteQueryOptions<
    Data, Error, InfiniteData<Data, PageParam>, QueryKey, PageParam
  >({
    queryKey,
    meta: sharedAuthoredMeta(client, queryKey, models),
    queryFn: (context) => requestAuthoredData<DocumentData<TDocument>>(
      dataProvider,
      dataProviderName,
      document,
      Object.assign({}, variables, context.pageParam),
      context,
    ),
    initialPageParam: null as TPage | null,
    getNextPageParam,
    placeholderData: undefined,
  });
}
