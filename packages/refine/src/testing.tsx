import * as React from "react";
import { Refine, type DataProvider, type RefineProps } from "@refinedev/core";
import { QueryClient, useQueryClient, type QueryClientConfig } from "@tanstack/react-query";
import { vi, type Mock } from "vitest";

/** Concrete fixture responses retain native envelopes, including missing-record reads. */
type RefineTestProviderResponse<K extends keyof DataProvider, Result> =
  K extends "custom" ? { data?: unknown } :
  K extends "getOne" ? { data?: Awaited<ReturnType<DataProvider["getOne"]>>["data"] | null } :
  Partial<Result>;

type RefineTestProviderArguments<Args extends readonly unknown[]> = {
  [I in keyof Args]: Partial<Args[I]>;
};

/** Fixtures consume a typed subset of native parameter fields and return concrete responses. */
export type RefineTestDataProvider = {
  [K in keyof DataProvider]?: {
    method(...args: RefineTestProviderArguments<Parameters<NonNullable<DataProvider[K]>>>):
      ReturnType<NonNullable<DataProvider[K]>> extends Promise<infer Result>
        ? Promise<RefineTestProviderResponse<K, Result>> | void
        : ReturnType<NonNullable<DataProvider[K]>>;
  }["method"];
};

export interface RefineTestProviderOptions<T extends RefineTestDataProvider = RefineTestDataProvider>
  extends Omit<RefineProps, "dataProvider" | "children"> {
  dataProvider?: T;
  apiUrl?: string;
  /** Direct QueryClient configuration; omit to retain Refine's native client defaults. */
  queryClientConfig?: QueryClientConfig;
  queryClient?: QueryClient;
  providerNames?: readonly string[];
}

type RefineTestProviderSpies = Record<"getOne" | "getList" | "create" | "update" | "deleteOne", Mock>;

/** Public harness contract keeps Vitest's internal inferred types out of declarations. */
export interface RefineTestProviders<T extends RefineTestDataProvider = Pick<RefineTestDataProvider, never>> {
  Provider: (props: RefineTestProviderOptions & { children?: React.ReactNode }) => React.ReactElement;
  dataProvider: DataProvider & Omit<RefineTestProviderSpies, keyof T> & T;
  clients: QueryClient[];
  createClient: (config?: QueryClientConfig) => QueryClient;
  clearClients: () => void;
}

/**
 * Compose native Refine providers with observable fixture methods. Each mount
 * gets a fresh client unless createClient supplies one for a live provider or
 * shared hook wrapper. Call clearClients after renderer cleanup to clear caches
 * and reset all mocks on factory and mounted providers, including overrides.
 * Refine-owned client options stay on options.reactQuery; direct client options
 * stay on queryClientConfig so upstream defaults are never reimplemented here.
 */
export function createRefineTestProviders<T extends RefineTestDataProvider = Pick<RefineTestDataProvider, never>>(
  defaults: RefineTestProviderOptions<T> = {},
): RefineTestProviders<T> {
  const clients: QueryClient[] = [];
  const providers = new Set<DataProvider>();
  const spies: RefineTestProviderSpies = {
    getOne: vi.fn(),
    getList: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    deleteOne: vi.fn(),
  };

  function composeDataProvider<Methods extends RefineTestDataProvider>(
    methods?: Methods,
    apiUrl = defaults.apiUrl ?? "test://refine",
  ) {
    const provider = { getApiUrl: () => apiUrl, ...spies, ...methods };
    // Fixtures implement concrete responses rather than DataProvider's generic promises.
    return provider as RefineTestProviders<Methods>["dataProvider"];
  }

  const dataProvider = composeDataProvider(defaults.dataProvider);

  function createClient(config: QueryClientConfig = {}): QueryClient {
    const client = new QueryClient(config);
    clients.push(client);
    return client;
  }

  function TrackClient({ provider }: { provider: DataProvider }) {
    const client = useQueryClient();
    React.useEffect(() => {
      if (!clients.includes(client)) clients.push(client);
      providers.add(provider);
    }, [client, provider]);
    return null;
  }

  function Provider({ children, ...overrides }: RefineTestProviderOptions & { children?: React.ReactNode }) {
    const {
      dataProvider: methods,
      apiUrl,
      queryClientConfig,
      queryClient,
      providerNames = [],
      options,
      ...refineProps
    } = { ...defaults, ...overrides };
    const [client] = React.useState(() => queryClient ?? (queryClientConfig ? new QueryClient(queryClientConfig) : undefined));
    const provider = React.useMemo(
      () => composeDataProvider({ ...defaults.dataProvider, ...methods }, apiUrl),
      [apiUrl, methods],
    );
    return <Refine
      {...refineProps}
      dataProvider={{ default: provider, ...Object.fromEntries(providerNames.map((name) => [name, provider])) }}
      options={{ ...options, disableTelemetry: true, reactQuery: {
        ...options?.reactQuery,
        ...(client ? { clientConfig: client } : {}),
      } }}
    >
      <TrackClient provider={provider} />
      {children}
    </Refine>;
  }

  return {
    Provider,
    dataProvider,
    clients,
    createClient,
    clearClients: () => {
      clients.splice(0).forEach((client) => client.clear());
      for (const provider of [dataProvider, ...providers]) {
        for (const method of Object.values(provider)) {
          if (vi.isMockFunction(method)) method.mockReset();
        }
      }
      providers.clear();
    },
  };
}
