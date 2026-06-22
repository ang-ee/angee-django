import { useCallback, useEffect, useState } from "react";
import type { DocumentInput } from "@urql/core";
import { useQuery as useUrqlQuery } from "urql";

import { DISABLED_DOCUMENTS } from "./disabled-documents";
import { useActiveGraphQLClientMaybe } from "./graphql-provider";

export interface DocumentQueryRun {
  data: unknown;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

const IDLE_QUERY_RUN: DocumentQueryRun = {
  data: undefined,
  fetching: false,
  error: null,
  refetch: () => {},
};

/**
 * The shared read seam: run one document with variables, or pause when not
 * enabled, exposing a uniform `{ data, fetching, error, refetch }`. Every read
 * hook (resource list/record, aggregates, authored queries) routes through this
 * so the run / pause / error-normalize / refetch logic lives in one place.
 *
 * `document` is urql's `DocumentInput`: a runtime-built query string (resource
 * list/record, aggregates) or a generated `TypedDocumentNode` (authored reads).
 */
export function useDocumentQuery(
  document: DocumentInput,
  variables: Record<string, unknown>,
  enabled: boolean,
): DocumentQueryRun {
  const [result, reexecute] = useUrqlQuery({
    query: enabled ? document : DISABLED_DOCUMENTS.query,
    variables,
    pause: !enabled,
    requestPolicy: "cache-first",
  });
  const refetch = useCallback(
    () => reexecute({ requestPolicy: "network-only" }),
    [reexecute],
  );
  return {
    data: result.data,
    fetching: result.fetching,
    error: result.error ?? null,
    refetch,
  };
}

/**
 * Optional read seam for derived affordances (facets, suggestions) that must be
 * inert outside a GraphQL provider. Required reads use `useDocumentQuery` and
 * still fail loudly when mounted without a provider.
 */
export function useOptionalDocumentQuery(
  document: DocumentInput,
  variables: Record<string, unknown>,
  enabled: boolean,
): DocumentQueryRun {
  const client = useActiveGraphQLClientMaybe();
  const [refetchId, setRefetchId] = useState(0);
  const [state, setState] = useState<DocumentQueryRun>(IDLE_QUERY_RUN);
  useEffect(() => {
    if (!enabled || !client) {
      setState(IDLE_QUERY_RUN);
      return;
    }
    let cancelled = false;
    setState((current) => ({
      ...current,
      fetching: true,
      error: null,
    }));
    void client
      .query(document, variables, {
        requestPolicy: refetchId === 0 ? "cache-first" : "network-only",
      })
      .toPromise()
      .then((result) => {
        if (cancelled) return;
        setState({
          data: result.data,
          fetching: false,
          error: result.error ?? null,
          refetch: () => setRefetchId((current) => current + 1),
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          data: undefined,
          fetching: false,
          error: error instanceof Error ? error : new Error(String(error)),
          refetch: () => setRefetchId((current) => current + 1),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [client, document, enabled, refetchId, variables]);
  return {
    ...state,
    refetch: useCallback(
      () => setRefetchId((current) => current + 1),
      [],
    ),
  };
}
