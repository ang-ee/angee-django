import { Alert, EmptyState, LoadingPanel, errorMessage } from "@angee/ui";
import {
  useAuthoredMutation,
  useAuthoredQuery,
  type DocumentData,
  type DocumentVariables,
} from "@angee/refine";
import type { TypedDocumentNode } from "@graphql-typed-document-node/core";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { OPERATOR_PROVIDER } from "./operator-provider";
import { useOperatorT } from "../i18n";
import { OperatorConnectionQuery } from "./documents";
import { useDocumentSubscription } from "./document-subscription";
import {
  SNAPSHOT_QUERY,
  STACK_SNAPSHOT_SUBSCRIPTION,
} from "./documents.daemon";
import { createOperatorClient, OperatorWsClientProvider } from "./operator-client";
import { operatorToken } from "./operator-token";
import type {
  OperatorConnectionInfo,
  OperatorSnapshot,
  OperatorSnapshotQueryData,
  OperatorSnapshotQueryVariables,
  OperatorSnapshotSections,
} from "./types";

const CONSOLE_SCHEMA = "console";
// Daemon connection tokens are short-lived (the daemon mints with a ~30m TTL).
// Refresh the bridge token well before then and rebuild the daemon client, so a
// long-running console never degrades to a dead token.
const CONNECTION_REFRESH_MS = 15 * 60_000;

type OperatorDocument = TypedDocumentNode<object, any>;
type OperatorVariables<TDocument extends OperatorDocument> =
  DocumentVariables<TDocument> extends Record<string, unknown>
    ? DocumentVariables<TDocument>
    : Record<string, never>;

// The one section→`@include` mapping: each snapshot pane and the matching
// `$want<Pane>` toggle in `SNAPSHOT_QUERY`. The hook derives its variables from
// this table so the pane list lives once (vs. an 8-line copy per concern).
const SNAPSHOT_SECTIONS = [
  "overview",
  "services",
  "workspaces",
  "sources",
  "gitOps",
  "operations",
  "templates",
  "secrets",
] as const satisfies readonly (keyof OperatorSnapshotSections)[];

/** The `$want<Pane>` toggle name for a pane (`gitOps` → `wantGitOps`). */
type WantVariable = `want${Capitalize<keyof OperatorSnapshotSections>}`;

/** The snapshot query's `@include` toggle name for a pane (`gitOps` → `wantGitOps`). */
function wantVariable(section: keyof OperatorSnapshotSections): WantVariable {
  return `want${section.charAt(0).toUpperCase()}${section.slice(1)}` as WantVariable;
}

/** The snapshot query's `@include` toggles — one per pane (`$wantOverview`…). */
type SnapshotVariables = OperatorSnapshotQueryVariables;

type ConnectionState =
  | { kind: "loading" }
  | { kind: "not-configured" }
  | { kind: "error"; message: string }
  | { kind: "ready"; connection: OperatorConnectionInfo };

/** The resolved daemon connection — the same-origin endpoint and minted bearer. */
export interface OperatorConnection {
  endpoint: string;
  token: string;
}
const OperatorConnectionContext = createContext<OperatorConnection | null>(null);

export interface OperatorTransportProviderProps {
  children: ReactNode;
}

export function OperatorTransportProvider({
  children,
}: OperatorTransportProviderProps): ReactNode {
  const t = useOperatorT();
  const connectionQuery = useAuthoredQuery(OperatorConnectionQuery, undefined, {
    dataProviderName: CONSOLE_SCHEMA,
  });

  useEffect(() => {
    const intervalId = globalThis.setInterval(() => {
      connectionQuery.refetch();
    }, CONNECTION_REFRESH_MS);
    return () => {
      globalThis.clearInterval(intervalId);
    };
  }, [connectionQuery.refetch]);

  const state = useMemo<ConnectionState>(() => {
    if (!connectionQuery.data && connectionQuery.fetching) {
      return { kind: "loading" };
    }
    if (connectionQuery.error) {
      return { kind: "error", message: connectionQuery.error.message };
    }
    try {
      const connection = parseOperatorConnection(connectionQuery.data?.operator_connection);
      return connection ? { kind: "ready", connection } : { kind: "not-configured" };
    } catch (error) {
      return {
        kind: "error",
        message: errorMessage(error, t("transport.unknownError")),
      };
    }
  }, [connectionQuery.data, connectionQuery.error, connectionQuery.fetching, t]);

  const endpoint = state.kind === "ready" ? state.connection.endpoint : null;
  const token = state.kind === "ready" ? state.connection.token : null;
  // Publish the live bearer to the module store the `operator` refine provider
  // reads per request (via `bearerAuthFromGetter`). This MUST run during render,
  // not in an effect: a child pane's first request fires in the child's mount
  // effect, which React runs before this parent's effects — an effect-time set
  // would race that request to a 401. Writing here each render also covers token
  // rotation. We do not null on unmount: only this gate's own subtree calls the
  // operator provider, so a lingering token is never read after the gate leaves,
  // and a null-on-unmount cleanup would be fired spuriously by StrictMode's
  // mount/unmount probe (leaving the store null for the real render in dev).
  operatorToken.set(token);
  // The daemon graphql-ws client for live subscriptions. Rebuilt on token
  // rotation (graphql-ws captures the bearer in connectionParams at connect, so a
  // new token needs a fresh socket) and absent without a WebSocket (SSR/test).
  const daemonClient = useMemo(() => {
    if (!endpoint || !token || typeof WebSocket === "undefined") return null;
    return createOperatorClient({ endpoint, token });
  }, [endpoint, token]);
  // Stable connection value for non-GraphQL transports; non-null exactly when
  // `daemonClient` is, so the `!daemonClient` guard below makes it present.
  const connection = useMemo<OperatorConnection | null>(
    () => (endpoint && token ? { endpoint, token } : null),
    [endpoint, token],
  );

  if (state.kind === "loading") {
    return <LoadingPanel message={t("transport.connecting")} />;
  }

  if (state.kind === "error") {
    return <Alert tone="danger">{state.message}</Alert>;
  }

  if (state.kind === "not-configured" || !daemonClient) {
    return (
      <EmptyState
        icon="operator"
        title={t("transport.unavailable.title")}
        description={t("transport.unavailable.description")}
      />
    );
  }

  return (
    <OperatorWsClientProvider value={daemonClient}>
      <OperatorConnectionContext.Provider value={connection}>
        {children}
      </OperatorConnectionContext.Provider>
    </OperatorWsClientProvider>
  );
}

/** The daemon connection (endpoint + bearer) for non-GraphQL transports (log sockets). */
export function useOperatorConnection(): OperatorConnection {
  const connection = useContext(OperatorConnectionContext);
  if (!connection) {
    throw new Error(
      "useOperatorConnection must be used inside OperatorTransportProvider.",
    );
  }
  return connection;
}

export interface OperatorSnapshotResult {
  result: { fetching: boolean; error: Error | null };
  snapshot: OperatorSnapshot | null;
  refetch: () => void;
}

export function useOperatorSnapshot(
  sections: OperatorSnapshotSections = { overview: true },
): OperatorSnapshotResult {
  // One signature over the requested panes keys the memo, so the variables
  // object stays referentially stable while the same panes are requested.
  const sectionsKey = SNAPSHOT_SECTIONS.map((section) =>
    sections[section] ? "1" : "0",
  ).join("");
  const variables = useMemo<SnapshotVariables>(
    () =>
      // Complete by construction — SNAPSHOT_SECTIONS is pinned to every pane key,
      // so the derived object carries every `WantVariable` the query requires.
      Object.fromEntries(
        SNAPSHOT_SECTIONS.map((section) => [
          wantVariable(section),
          sections[section] ?? false,
        ]),
      ) as SnapshotVariables,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sectionsKey],
  );

  // The one-shot query owns first paint: the daemon emits no snapshot on connect,
  // so the console reads the current state once through the `operator` provider.
  // The live subscription supersedes it for every subsequent change (no polling —
  // see docs/frontend/guidelines.md).
  const query = useAuthoredQuery(SNAPSHOT_QUERY, variables, {
    dataProviderName: OPERATOR_PROVIDER,
  });

  // `onStackSnapshotChange` pushes the whole `StackSnapshot` whenever the daemon's
  // aggregate hash changes; the variable-free document shares one upstream socket
  // across the 8 panes. The latest push is the live snapshot.
  const live = useOperatorSubscription(STACK_SNAPSHOT_SUBSCRIPTION);

  // An imperative re-pull of the one-shot query, for an instant local refresh
  // after a mutation rather than waiting for the daemon's next snapshot push.
  const refetch = query.refetch;

  // Keep whichever source updated most recently. The live push wins as it
  // arrives, but an explicit refetch (after a mutation) must be able to supersede
  // a stale push — a static `live ?? query` would mask refetch forever once any
  // push landed.
  const [data, setData] = useState<OperatorSnapshotQueryData | null>(null);
  useEffect(() => {
    const pushed = live.data?.onStackSnapshotChange;
    if (pushed) setData(pushed);
  }, [live.data]);
  useEffect(() => {
    if (query.data) setData(query.data);
  }, [query.data]);
  const snapshot = useMemo(() => snapshotFromQueryData(data), [data]);

  return {
    result: { fetching: query.fetching, error: query.error },
    snapshot,
    refetch,
  };
}

export interface OperatorActionHook<TDocument extends OperatorDocument> {
  result: { fetching: boolean; error: Error | null };
  run: (
    variables: OperatorVariables<TDocument>,
  ) => Promise<DocumentData<TDocument> | undefined>;
}

/**
 * A daemon mutation on the `operator` refine data provider. Wraps
 * `useAuthoredMutation` (the shared authored-mutation owner) bound to the daemon
 * provider so every command RPC and Hasura CRUD doc rides the same bearer-authed
 * transport as the rest of the console. The daemon refreshes its own state
 * imperatively (`useRunDaemonAction` → snapshot refetch), not through refine's
 * resource invalidation, so no `invalidateModels` is wired here.
 */
export function useOperatorAction<TDocument extends OperatorDocument>(
  document: TDocument,
): OperatorActionHook<TDocument> {
  type Variables = OperatorVariables<TDocument>;
  const [mutate, state] = useAuthoredMutation(document, {
    dataProviderName: OPERATOR_PROVIDER,
  });
  const run = useCallback(
    (variables: Variables) => mutate(variables),
    [mutate],
  );
  return { result: state, run };
}

export interface OperatorSubscriptionOptions<TData> {
  enabled?: boolean;
  onData?: (data: TData) => void;
}

export interface OperatorSubscriptionResult<TData> {
  data: TData | undefined;
  fetching: boolean;
  error: Error | null;
}

export function useOperatorSubscription<TDocument extends OperatorDocument>(
  document: TDocument,
  variables?: OperatorVariables<TDocument>,
  options?: OperatorSubscriptionOptions<DocumentData<TDocument>>,
): OperatorSubscriptionResult<DocumentData<TDocument>>;
export function useOperatorSubscription<
  TData extends object,
  TVariables extends Record<string, unknown> = Record<string, never>,
>(
  document: string,
  variables?: TVariables,
  options?: OperatorSubscriptionOptions<TData>,
): OperatorSubscriptionResult<TData>;
export function useOperatorSubscription<
  TData extends object,
  TVariables extends Record<string, unknown>,
>(
  document: OperatorDocument | string,
  variables?: TVariables,
  options: OperatorSubscriptionOptions<TData> = {},
): OperatorSubscriptionResult<TData> {
  // Delegate to the document subscription runner over the daemon graphql-ws
  // client: it fires `onData` from the socket's `next` callback (a real event, not
  // a render-phase reducer), so a caller can set React state in it.
  return useDocumentSubscription<TData, TVariables>(document, variables, options);
}

// The daemon exposes its state as separate root fields; assemble the roots each
// pane requested into one snapshot, defaulting absent (un-`@include`d) lists.
// Accepts the query data or a live subscription push (same root shape).
function snapshotFromQueryData(
  data: OperatorSnapshotQueryData | null | undefined,
): OperatorSnapshot | null {
  if (!data) return null;
  return {
    health: data.health ?? null,
    stack: data.stackStatus ?? null,
    services: data.services ?? [],
    jobs: data.jobs ?? [],
    sources: data.sources ?? [],
    workspaces: data.workspaces ?? [],
    templates: data.templates ?? [],
    secrets: data.secrets ?? [],
    gitOps: data.gitOpsTopology ?? null,
  };
}

function parseOperatorConnection(value: unknown): OperatorConnectionInfo | null {
  if (value == null) return null;
  if (!isRecord(value)) {
    throw new Error("operatorConnection returned an invalid payload.");
  }
  const endpoint = value.endpoint;
  const token = value.token;
  if (typeof endpoint !== "string" || typeof token !== "string") {
    throw new Error("operatorConnection is missing endpoint or token.");
  }
  return { endpoint, token };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
