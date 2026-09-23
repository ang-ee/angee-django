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
  queryOptions,
  useQueryClient,
  type DataTag,
  type Query,
  type QueryClient,
  type QueryFunctionContext,
  type QueryKey,
  type QueryOptions,
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
    // Consume Query's signal only if the provider can use it. Reading it here
    // would cancel a shared request on unmount even when transport keeps running.
    meta: {
      ...queryMeta(document, variables),
      queryKey: context.queryKey,
      get signal() { return context.signal; },
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
  records: readonly { model: string; id: string }[] = [],
  relatedModels: readonly string[] = [],
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
    meta: sharedAuthoredMeta(client, queryKey, models, records, relatedModels),
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
  records: readonly { model: string; id: string }[] = [],
  relatedModels: readonly string[] = [],
  { gcTime }: Pick<QueryOptions, "gcTime"> = {},
) {
  // Query only extends GC lifetimes, so apply an explicit lifetime on first build.
  const defaulted = client.defaultQueryOptions({ queryKey, ...(gcTime === undefined ? {} : { gcTime }) });
  const query = client.getQueryCache().build(client, defaulted);
  // Host defaults may share a single meta object across every query. This entry
  // needs its own object before observers can union interests in place.
  const meta = !query.meta || query.meta === defaulted.meta
    ? { ...query.meta }
    : query.meta;
  const previous = Array.isArray(meta.angeeModels)
    ? meta.angeeModels as string[]
    : [];
  const previousRecords = Array.isArray(meta.angeeRecords) ? meta.angeeRecords : [];
  const previousBroad = Array.isArray(meta.angeeBroadModels)
    ? meta.angeeBroadModels as string[]
    : previous.length > 0 && !Array.isArray(meta.angeeRecords)
      ? previous
      : [];
  const previousRelated = Array.isArray(meta.angeeRelatedModels)
    ? meta.angeeRelatedModels as string[]
    : [];
  meta.angeeModels = [...new Set([...previous, ...models])].sort();
  const exactModels = new Set([
    ...records.map((record) => record.model),
    ...relatedModels,
  ]);
  const broadModels = [...new Set([
    ...previousBroad,
    ...models.filter((model) => !exactModels.has(model)),
  ])].sort();
  const exactRelatedModels = [...new Set([...previousRelated, ...relatedModels])]
    .filter((model) => !broadModels.includes(model))
    .sort();
  const exactRecords = [...new Map([...previousRecords, ...records].map((record) => {
      const value = record as { model: string; id: string };
      return [`${value.model}:${value.id}`, value];
    })).values()].filter((record) => !broadModels.includes(record.model));
  if (broadModels.length > 0) meta.angeeBroadModels = broadModels;
  if (exactRelatedModels.length > 0) meta.angeeRelatedModels = exactRelatedModels;
  else delete meta.angeeRelatedModels;
  if (exactRecords.length > 0) {
    meta.angeeRecords = exactRecords;
  } else {
    delete meta.angeeRecords;
  }
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
