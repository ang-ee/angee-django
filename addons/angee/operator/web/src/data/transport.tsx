import { Alert, EmptyState, LoadingPanel } from "@angee/base";
import { errorMessage, useSchemaClients } from "@angee/sdk";
import {
  Provider as UrqlProvider,
  useMutation,
  useQuery,
  useSubscription,
  type Client,
  type OperationResult,
  type UseMutationState,
  type UseQueryState,
} from "urql";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useOperatorT } from "../i18n";
import {
  OPERATOR_CONNECTION_QUERY,
  SNAPSHOT_QUERY,
  STACK_SNAPSHOT_SUBSCRIPTION,
} from "./documents";
import { createOperatorClient } from "./operator-client";
import type {
  OperatorConnectionInfo,
  OperatorSnapshot,
  OperatorSnapshotQueryData,
  OperatorSnapshotSections,
  OperatorSnapshotSubscriptionData,
} from "./types";

const CONSOLE_SCHEMA = "console";
// Daemon connection tokens are short-lived (the daemon mints with a ~30m TTL).
// Refresh the bridge token well before then and rebuild the daemon client, so a
// long-running console never degrades to a dead token.
const CONNECTION_REFRESH_MS = 15 * 60_000;

type EmptyVariables = Record<string, never>;

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
type SnapshotVariables = Record<WantVariable, boolean>;

interface OperatorConnectionQueryData {
  operatorConnection?: unknown;
}

type ConnectionState =
  | { kind: "loading" }
  | { kind: "not-configured" }
  | { kind: "error"; message: string }
  | { kind: "ready"; connection: OperatorConnectionInfo };

const OperatorClientContext = createContext<Client | null>(null);

export interface OperatorTransportProviderProps {
  children: ReactNode;
}

export function OperatorTransportProvider({
  children,
}: OperatorTransportProviderProps): ReactNode {
  const t = useOperatorT();
  const clients = useSchemaClients();
  const consoleClient = clients[CONSOLE_SCHEMA];
  const [state, setState] = useState<ConnectionState>({ kind: "loading" });

  // Fetch the daemon endpoint + a freshly minted scoped token. `network-only` so
  // an expired token is never served from the console client's cache.
  const loadConnection = useCallback(
    async (signal: { active: boolean }) => {
      if (!consoleClient) {
        setState({
          kind: "error",
          message: t("operator.transport.noConsoleClient"),
        });
        return;
      }
      try {
        const result = await consoleClient
          .query<OperatorConnectionQueryData, EmptyVariables>(
            OPERATOR_CONNECTION_QUERY,
            {},
            { requestPolicy: "network-only" },
          )
          .toPromise();
        if (!signal.active) return;
        if (result.error) {
          setState({ kind: "error", message: result.error.message });
          return;
        }
        const connection = parseOperatorConnection(result.data?.operatorConnection);
        setState(
          connection ? { kind: "ready", connection } : { kind: "not-configured" },
        );
      } catch (error: unknown) {
        if (!signal.active) return;
        setState({ kind: "error", message: errorMessage(error, t("operator.transport.unknownError")) });
      }
    },
    [consoleClient, t],
  );

  useEffect(() => {
    const signal = { active: true };
    setState({ kind: "loading" });
    void loadConnection(signal);
    const intervalId = globalThis.setInterval(() => {
      void loadConnection(signal);
    }, CONNECTION_REFRESH_MS);
    return () => {
      signal.active = false;
      globalThis.clearInterval(intervalId);
    };
  }, [loadConnection]);

  const endpoint = state.kind === "ready" ? state.connection.endpoint : null;
  const token = state.kind === "ready" ? state.connection.token : null;
  // Rebuilt whenever the token rotates, so daemon requests carry the live bearer.
  const daemonClient = useMemo(() => {
    if (!endpoint || !token) return null;
    return createOperatorClient({ endpoint, token });
  }, [endpoint, token]);

  if (state.kind === "loading") {
    return <LoadingPanel message={t("operator.transport.connecting")} />;
  }

  if (state.kind === "error") {
    return <Alert tone="danger">{state.message}</Alert>;
  }

  if (state.kind === "not-configured" || !daemonClient) {
    return (
      <EmptyState
        icon="operator"
        title={t("operator.transport.unavailable.title")}
        description={t("operator.transport.unavailable.description")}
      />
    );
  }

  return (
    <OperatorClientContext.Provider value={daemonClient}>
      <UrqlProvider value={daemonClient}>{children}</UrqlProvider>
    </OperatorClientContext.Provider>
  );
}

export function useOperatorClient(): Client {
  const client = useContext(OperatorClientContext);
  if (!client) {
    throw new Error(
      "useOperatorClient must be used inside OperatorTransportProvider.",
    );
  }
  return client;
}

export interface OperatorSnapshotResult {
  result: UseQueryState<OperatorSnapshotQueryData, SnapshotVariables>;
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
  // so the console reads the current state once. The live subscription supersedes
  // it for every subsequent change (no polling — see docs/frontend/guidelines.md).
  const [result, reexecute] = useQuery<OperatorSnapshotQueryData, SnapshotVariables>({
    query: SNAPSHOT_QUERY,
    variables,
    requestPolicy: "cache-and-network",
  });

  // `onStackSnapshotChange` pushes the whole `StackSnapshot` whenever the daemon's
  // aggregate hash changes; urql dedupes the (variable-free) document so the 8 panes
  // share one upstream subscription. The latest push is the live snapshot.
  const live = useOperatorSubscription<OperatorSnapshotSubscriptionData>(
    STACK_SNAPSHOT_SUBSCRIPTION,
  );

  const reexecuteRef = useRef(reexecute);
  useEffect(() => {
    reexecuteRef.current = reexecute;
  }, [reexecute]);

  // An imperative re-pull of the one-shot query, for an instant local refresh
  // after a mutation rather than waiting for the daemon's next snapshot push.
  const refetch = useCallback(() => {
    reexecuteRef.current({ requestPolicy: "network-only" });
  }, []);

  // Keep whichever source updated most recently. The live push wins as it
  // arrives, but an explicit refetch (network-only, after a mutation) must be
  // able to supersede a stale push — a static `live ?? query` would mask refetch
  // forever once any push landed.
  const [data, setData] = useState<OperatorSnapshotQueryData | null>(null);
  useEffect(() => {
    const pushed = live.data?.onStackSnapshotChange;
    if (pushed) setData(pushed);
  }, [live.data]);
  useEffect(() => {
    if (result.data) setData(result.data);
  }, [result.data]);
  const snapshot = useMemo(() => snapshotFromQueryData(data), [data]);

  return { result, snapshot, refetch };
}

export interface OperatorActionHook<
  Data extends object,
  Variables extends Record<string, unknown>,
> {
  result: UseMutationState<Data, Variables>;
  run: (variables: Variables) => Promise<Data>;
}

export function useOperatorAction<
  Data extends object,
  Variables extends Record<string, unknown> = EmptyVariables,
>(document: string): OperatorActionHook<Data, Variables> {
  const [result, execute] = useMutation<Data, Variables>(document);
  const run = useCallback(
    async (variables: Variables): Promise<Data> => {
      const operationResult: OperationResult<Data, Variables> =
        await execute(variables);
      if (operationResult.error) {
        throw operationResult.error;
      }
      if (!operationResult.data) {
        throw new Error("Operator action returned no data.");
      }
      return operationResult.data;
    },
    [execute],
  );
  return { result, run };
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

export function useOperatorSubscription<
  TData extends object,
  TVariables extends Record<string, unknown> = EmptyVariables,
>(
  document: string,
  variables?: TVariables,
  options: OperatorSubscriptionOptions<TData> = {},
): OperatorSubscriptionResult<TData> {
  const enabled = options.enabled ?? true;
  const { onData } = options;
  const [state] = useSubscription<TData, TData, TVariables>(
    {
      query: document,
      variables: (variables ?? {}) as TVariables,
      pause: !enabled,
    },
    (_previous, value) => {
      onData?.(value);
      return value;
    },
  );
  return {
    data: state.data,
    fetching: state.fetching,
    error: state.error ?? null,
  };
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
