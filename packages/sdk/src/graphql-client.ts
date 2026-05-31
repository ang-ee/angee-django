import { cacheExchange } from "@urql/exchange-graphcache";
import { createClient as createWSClient } from "graphql-ws";
import {
  createClient,
  fetchExchange,
  subscriptionExchange,
  type Client,
  type Exchange,
} from "urql";

import type { CacheConfig } from "./cache-config";

type FetchFn = typeof globalThis.fetch;

/** Derive the GraphQL-over-WebSocket URL from an http(s) endpoint. */
export function graphQLWebSocketUrl(endpoint: string, origin?: string): string {
  const base =
    origin ?? (typeof location !== "undefined" ? location.origin : undefined);
  const url = new URL(endpoint, base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

/** A deduplicating, cacheable source of the Django CSRF token. */
export interface CsrfTokenProvider {
  token(): Promise<string | null>;
  clear(): void;
}

export interface CsrfTokenOptions {
  endpoint?: string;
  fetch?: FetchFn;
}

/**
 * Fetch the CSRF token from `endpoint` once, sharing one in-flight request
 * across concurrent callers and caching the result until `clear()`.
 */
export function createCsrfTokenProvider(
  options: CsrfTokenOptions = {},
): CsrfTokenProvider {
  const endpoint = options.endpoint ?? "/auth/csrf/";
  const fetchImpl = options.fetch ?? globalThis.fetch;
  let cached: string | null = null;
  let inFlight: Promise<string | null> | null = null;

  async function load(): Promise<string | null> {
    const response = await fetchImpl(endpoint, { credentials: "include" });
    if (!response.ok) return null;
    const body = (await response.json()) as { token?: unknown };
    return typeof body.token === "string" ? body.token : null;
  }

  return {
    async token() {
      if (cached !== null) return cached;
      inFlight ??= load().then((token) => {
        cached = token;
        inFlight = null;
        return token;
      });
      return inFlight;
    },
    clear() {
      cached = null;
      inFlight = null;
    },
  };
}

export interface AngeeUrqlClientOptions {
  /** HTTP GraphQL endpoint for this named schema. */
  url: string;
  /** WebSocket endpoint; derived from `url` when omitted. */
  wsEndpoint?: string;
  /** Schema-derived graphcache keying + relay resolvers. */
  cache?: CacheConfig;
  /** CSRF token endpoint; defaults to `/auth/csrf/`. */
  csrfEndpoint?: string;
  /** Injected for tests; defaults to the global fetch. */
  fetch?: FetchFn;
  /**
   * Override the exchange stack. The default wires the normalized cache, the
   * subscription transport, and fetch; supply this for SSR or to run without the
   * cache. `(fetch) => Exchange[]` receives the session-aware fetch exchange.
   */
  exchanges?: Exchange[];
}

/**
 * Build the urql client for one named schema: a configured normalized cache, a
 * graphql-ws subscription transport, and an HTTP transport whose requests carry
 * the session cookie and CSRF header. The cache must be configured (keys +
 * relay resolvers) for normalized reads and pagination to work.
 */
export function createUrqlClient(options: AngeeUrqlClientOptions): Client {
  const baseFetch = options.fetch ?? globalThis.fetch;
  const csrf = createCsrfTokenProvider({
    endpoint: options.csrfEndpoint,
    fetch: baseFetch,
  });

  const fetchWithSession: FetchFn = async (input, init) => {
    const headers = new Headers(init?.headers);
    if (!headers.has("x-csrftoken")) {
      const token = await csrf.token();
      if (token) headers.set("x-csrftoken", token);
    }
    return baseFetch(input, { ...init, credentials: "include", headers });
  };

  const cache = options.cache ?? { keys: {}, resolvers: {} };

  return createClient({
    url: options.url,
    fetch: fetchWithSession,
    exchanges: options.exchanges ?? [
      cacheExchange({ keys: cache.keys, resolvers: cache.resolvers }),
      subscriptionExchange({
        forwardSubscription: subscriptionForwarder(
          options.wsEndpoint ?? options.url,
        ),
      }),
      fetchExchange,
    ],
  });
}

const FATAL_WS_CLOSE_CODES = new Set([1000, 1008, 4400, 4401, 4403, 4406, 4409]);

interface ForwardedSubscription {
  query?: string;
  variables?: Record<string, unknown>;
  operationName?: string;
  extensions?: Record<string, unknown>;
}

/**
 * A graphql-ws-backed forwarder for urql's subscriptionExchange. The WS URL is
 * resolved only when a WebSocket transport exists, so building a client in a
 * non-browser context (tests, SSR) needs no DOM origin.
 */
function subscriptionForwarder(endpoint: string) {
  if (typeof WebSocket === "undefined") {
    return () => ({ subscribe: () => ({ unsubscribe() {} }) });
  }
  const wsClient = createWSClient({
    url: graphQLWebSocketUrl(endpoint),
    lazy: true,
    shouldRetry: (event) =>
      !(event instanceof CloseEvent && FATAL_WS_CLOSE_CODES.has(event.code)),
  });
  return (request: ForwardedSubscription) => ({
    subscribe(sink: {
      next: (value: unknown) => void;
      error: (error: unknown) => void;
      complete: () => void;
    }) {
      const unsubscribe = wsClient.subscribe(
        { ...request, query: request.query ?? "" },
        sink,
      );
      return { unsubscribe };
    },
  });
}
