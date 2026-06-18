import { useEffect, useRef } from "react";
import type { DocumentInput } from "@urql/core";
import { useSubscription as useUrqlSubscription } from "urql";

import { DISABLED_DOCUMENTS } from "./disabled-documents";
import { useStableVariables } from "./stable-deps";

export interface DocumentSubscriptionOptions<TData> {
  enabled?: boolean;
  onData?: (data: TData) => void;
}

export interface DocumentSubscriptionRun<TData> {
  data: TData | undefined;
  fetching: boolean;
  error: Error | null;
}

interface SubscriptionEvent<TData> {
  data: TData;
  version: number;
}

/**
 * The shared subscription seam for generated `TypedDocumentNode`s and runtime-built
 * subscription strings. `onData` fires from an effect once per push, never from
 * urql's reducer, so callers can safely set React state in it.
 */
export function useDocumentSubscription<
  TData = unknown,
  TVariables extends Record<string, unknown> = Record<string, unknown>,
>(
  document: DocumentInput<TData, TVariables>,
  variables?: TVariables,
  options: DocumentSubscriptionOptions<TData> = {},
): DocumentSubscriptionRun<TData> {
  const enabled = options.enabled ?? true;
  const stable = useStableVariables(variables);
  const { onData } = options;
  const onDataRef = useRef(onData);
  onDataRef.current = onData;
  const [state] = useUrqlSubscription<TData, SubscriptionEvent<TData>, TVariables>(
    {
      query: enabled ? document : DISABLED_DOCUMENTS.subscription,
      variables: stable,
      pause: !enabled,
    },
    (_previous, value) => {
      return {
        data: value,
        version: (_previous?.version ?? 0) + 1,
      };
    },
  );
  const event = state.data;
  useEffect(() => {
    if (event) onDataRef.current?.(event.data);
  }, [event]);
  return {
    data: event?.data,
    fetching: state.fetching,
    error: state.error ?? null,
  };
}
