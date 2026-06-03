import { Spinner } from "@angee/base";
import { useSchemaClients } from "@angee/sdk";
import {
  Provider as UrqlProvider,
  useMutation,
  useQuery,
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

import { OPERATOR_CONNECTION_QUERY, SNAPSHOT_QUERY } from "./documents";
import { createOperatorClient } from "./operator-client";
import type {
  OperatorConnectionInfo,
  OperatorSnapshot,
  OperatorSnapshotQueryData,
  OperatorSnapshotSections,
} from "./types";

const CONSOLE_SCHEMA = "console";
const POLL_INTERVAL_MS = 5_000;
// Daemon connection tokens are short-lived (the daemon mints with a ~30m TTL).
// Refresh the bridge token well before then and rebuild the daemon client, so a
// long-running console never degrades to a dead token.
const CONNECTION_REFRESH_MS = 15 * 60_000;

type EmptyVariables = Record<string, never>;

/** The snapshot query's `@include` toggles — one per pane. */
interface SnapshotVariables {
  wantOverview: boolean;
  wantServices: boolean;
  wantWorkspaces: boolean;
  wantSources: boolean;
  wantGitOps: boolean;
  wantOperations: boolean;
  wantTemplates: boolean;
  wantSecrets: boolean;
  [key: string]: boolean;
}

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
          message: 'No "console" GraphQL client is configured.',
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
        setState({ kind: "error", message: messageFromUnknown(error) });
      }
    },
    [consoleClient],
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
    return (
      <div
        aria-live="polite"
        className="flex min-h-48 items-center justify-center gap-3 text-sm text-fg-muted"
        role="status"
      >
        <Spinner size="md" tone="brand" />
        <span>Connecting to operator</span>
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="rounded-md border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">
        {state.message}
      </div>
    );
  }

  if (state.kind === "not-configured" || !daemonClient) {
    return (
      <div className="rounded-md border border-border bg-surface px-4 py-3 text-sm text-fg-muted">
        Operator daemon is not configured for this user.
      </div>
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
  const wantOverview = sections.overview ?? false;
  const wantServices = sections.services ?? false;
  const wantWorkspaces = sections.workspaces ?? false;
  const wantSources = sections.sources ?? false;
  const wantGitOps = sections.gitOps ?? false;
  const wantOperations = sections.operations ?? false;
  const wantTemplates = sections.templates ?? false;
  const wantSecrets = sections.secrets ?? false;

  const variables = useMemo<SnapshotVariables>(
    () => ({
      wantOverview,
      wantServices,
      wantWorkspaces,
      wantSources,
      wantGitOps,
      wantOperations,
      wantTemplates,
      wantSecrets,
    }),
    [
      wantOverview,
      wantServices,
      wantWorkspaces,
      wantSources,
      wantGitOps,
      wantOperations,
      wantTemplates,
      wantSecrets,
    ],
  );

  const [result, reexecute] = useQuery<OperatorSnapshotQueryData, SnapshotVariables>({
    query: SNAPSHOT_QUERY,
    variables,
    requestPolicy: "cache-and-network",
  });

  const reexecuteRef = useRef(reexecute);
  const fetchingRef = useRef(result.fetching);
  useEffect(() => {
    reexecuteRef.current = reexecute;
    fetchingRef.current = result.fetching;
  }, [reexecute, result.fetching]);

  useEffect(() => {
    const intervalId = globalThis.setInterval(() => {
      // Skip the tick while a request is already in flight. The daemon's
      // git-backed resolvers (sources, gitOps) can take longer than the poll
      // interval, and a network-only reexecute aborts the pending request — so
      // an unconditional poll would cancel each fetch before it ever resolves
      // and the snapshot would never settle.
      if (!fetchingRef.current) {
        reexecuteRef.current({ requestPolicy: "network-only" });
      }
    }, POLL_INTERVAL_MS);
    return () => globalThis.clearInterval(intervalId);
  }, []);

  const refetch = useCallback(() => {
    reexecuteRef.current({ requestPolicy: "network-only" });
  }, []);

  const snapshot = useMemo(() => snapshotFromQueryData(result.data), [result.data]);

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

// The daemon exposes its state as separate root fields; assemble the roots each
// pane requested into one snapshot, defaulting absent (un-`@include`d) lists.
function snapshotFromQueryData(
  data: OperatorSnapshotQueryData | undefined,
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

function messageFromUnknown(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown operator error.";
}
