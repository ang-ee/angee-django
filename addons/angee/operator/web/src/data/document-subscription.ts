import { useEffect, useMemo, useRef, useState } from "react";
import { print, type DocumentNode } from "graphql";
import type { TypedDocumentNode } from "@graphql-typed-document-node/core";
import { useStableVariables } from "@angee/refine";

import { useOperatorWsClient } from "./operator-client";

type DocumentInput<TData, TVariables> =
  | string
  | DocumentNode
  | TypedDocumentNode<TData, TVariables>;

export interface DocumentSubscriptionOptions<TData> {
  enabled?: boolean;
  onData?: (data: TData) => void;
}

export interface DocumentSubscriptionRun<TData> {
  data: TData | undefined;
  fetching: boolean;
  error: Error | null;
}

/**
 * Daemon subscription seam for generated `TypedDocumentNode`s and runtime-built
 * subscription strings, over the daemon graphql-ws client. `onData` fires on each
 * push from the socket's `next` callback (a real event, not a render-phase
 * reducer), so callers can safely set React state in it.
 */
export function useDocumentSubscription<
  TData = unknown,
  TVariables extends Record<string, unknown> = Record<string, unknown>,
>(
  document: DocumentInput<TData, TVariables>,
  variables?: TVariables,
  options: DocumentSubscriptionOptions<TData> = {},
): DocumentSubscriptionRun<TData> {
  const client = useOperatorWsClient();
  const enabled = options.enabled ?? true;
  const stable = useStableVariables(variables);
  const onDataRef = useRef(options.onData);
  onDataRef.current = options.onData;
  const query = useMemo(
    () => (typeof document === "string" ? document : print(document)),
    [document],
  );
  const [state, setState] = useState<DocumentSubscriptionRun<TData>>({
    data: undefined,
    fetching: enabled,
    error: null,
  });

  useEffect(() => {
    if (!enabled) {
      setState({ data: undefined, fetching: false, error: null });
      return;
    }
    setState((prev) => ({ ...prev, fetching: true, error: null }));
    const dispose = client.subscribe<TData>(
      { query, variables: stable },
      {
        next: (result) => {
          const data = (result.data ?? undefined) as TData | undefined;
          if (data === undefined) return;
          setState({ data, fetching: true, error: null });
          onDataRef.current?.(data);
        },
        error: (caught) => {
          setState((prev) => ({ ...prev, fetching: false, error: toError(caught) }));
        },
        complete: () => {
          setState((prev) => ({ ...prev, fetching: false }));
        },
      },
    );
    return () => dispose();
  }, [client, query, stable, enabled]);

  return state;
}

function toError(value: unknown): Error {
  if (value instanceof Error) return value;
  if (Array.isArray(value)) {
    const message = value
      .map((item) =>
        item && typeof item === "object" && "message" in item
          ? String((item as { message: unknown }).message)
          : String(item),
      )
      .join("; ");
    return new Error(message || "Daemon subscription error");
  }
  if (typeof value === "string") return new Error(value);
  return new Error("Daemon subscription error");
}
