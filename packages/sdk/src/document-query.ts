import { useCallback } from "react";
import { useQuery as useUrqlQuery } from "urql";

import { DISABLED_DOCUMENTS } from "./disabled-documents";

export interface DocumentQueryRun {
  data: unknown;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

/**
 * The shared read seam: run one document with variables, or pause when not
 * enabled, exposing a uniform `{ data, fetching, error, refetch }`. Every read
 * hook (resource list/record, aggregates, authored queries) routes through this
 * so the run / pause / error-normalize / refetch logic lives in one place.
 */
export function useDocumentQuery(
  document: string,
  variables: Record<string, unknown>,
  enabled: boolean,
): DocumentQueryRun {
  const [result, reexecute] = useUrqlQuery({
    query: enabled ? document : DISABLED_DOCUMENTS.query,
    variables,
    pause: !enabled,
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
