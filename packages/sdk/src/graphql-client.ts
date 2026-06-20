import { cacheExchange } from "@urql/exchange-graphcache";
import { buildSchema } from "graphql";
import { createClient as createWSClient } from "graphql-ws";
import {
  createClient,
  fetchExchange,
  subscriptionExchange,
  type Client,
  type Exchange,
  type ExecutionResult,
} from "@urql/core";
import type { FetchBody } from "@urql/core/internal";

import { cacheConfigFromSchema, type CacheConfig } from "./cache-config";

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

/**
 * Decorates a base fetch with one transport's auth — its headers and
 * credentials. The single axis on which a same-origin app client and a
 * cross-origin service client (e.g. the operator daemon) differ; everything else
 * about the client is shared, so this is the only thing a caller swaps.
 */
export type AuthFetch = (baseFetch: FetchFn) => FetchFn;

/**
 * Session auth — the app default. Sends the Django session cookie and a lazily
 * fetched, cached CSRF header. Each decorated fetch owns its CSRF provider, so
 * rebuilding the client (a login/logout reset) discards the cached token.
 */
export function sessionAuth(options: CsrfTokenOptions = {}): AuthFetch {
  return (baseFetch) => {
    const csrf = createCsrfTokenProvider({
      endpoint: options.endpoint,
      fetch: options.fetch ?? baseFetch,
    });
    return async (input, init) => {
      const headers = new Headers(init?.headers);
      if (!headers.has("x-csrftoken")) {
        const token = await csrf.token();
        if (token) headers.set("x-csrftoken", token);
      }
      return baseFetch(input, { ...init, credentials: "include", headers });
    };
  };
}

/**
 * Bearer auth — for a cross-origin service whose minted token is the credential
 * (the operator daemon). Sends `Authorization: Bearer <token>` with no cookie or
 * CSRF; rebuild the client to rotate the token.
 */
export function bearerAuth(token: string): AuthFetch {
  return (baseFetch) => (input, init) => {
    const headers = new Headers(init?.headers);
    headers.set("Authorization", `Bearer ${token}`);
    return baseFetch(input, { ...init, headers });
  };
}

export interface AngeeUrqlClientOptions {
  /** HTTP GraphQL endpoint for this named schema. */
  url: string;
  /** Printed GraphQL SDL for this schema; derives cache and field metadata. */
  sdl?: string;
  /** WebSocket endpoint; derived from `url` when omitted. */
  wsEndpoint?: string;
  /** Schema-derived graphcache keying + connection resolvers. */
  cache?: CacheConfig;
  /**
   * Transport auth strategy; defaults to {@link sessionAuth} (Django cookie +
   * CSRF). Pass {@link bearerAuth} for a token-authenticated service.
   */
  auth?: AuthFetch;
  /** CSRF token endpoint for the default session auth; defaults to `/auth/csrf/`. */
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
 * graphql-ws subscription transport, and an HTTP transport authed by `auth`
 * (the Django session cookie + CSRF header by default). The cache must be
 * configured (keys + connection resolvers) for normalized reads and pagination to
 * work.
 */
export function createUrqlClient(options: AngeeUrqlClientOptions): Client {
  const baseFetch = options.fetch ?? globalThis.fetch;
  const auth = options.auth ?? sessionAuth({ endpoint: options.csrfEndpoint });
  const cache = options.cache ?? (
    options.sdl ? cacheConfigFromSchema(buildSchema(options.sdl)) : { keys: {}, resolvers: {} }
  );

  return createClient({
    url: options.url,
    fetch: auth(baseFetch),
    // Always POST; the Django endpoint is CSRF-protected and reads operations
    // from the request body rather than the query string.
    preferGetMethod: false,
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

/** Whether a graphql-ws close code is terminal rather than retryable. */
export function isFatalGraphQLWsCloseCode(code: number): boolean {
  return FATAL_WS_CLOSE_CODES.has(code);
}

/** Whether a graphql-ws close event is terminal rather than retryable. */
export function isFatalGraphQLWsClose(event: unknown): boolean {
  return (
    typeof CloseEvent !== "undefined" &&
    event instanceof CloseEvent &&
    isFatalGraphQLWsCloseCode(event.code)
  );
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
    shouldRetry: (event) => !isFatalGraphQLWsClose(event),
  });
  return (request: FetchBody) => ({
    subscribe(sink: {
      next: (value: ExecutionResult) => void;
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
