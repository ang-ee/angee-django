import {
  cacheExchange,
  createClient,
  fetchExchange,
  type Client,
} from "urql";

import type { OperatorConnectionInfo } from "./types";

type FetchFn = typeof globalThis.fetch;

export function createOperatorClient(connection: OperatorConnectionInfo): Client {
  const fetchWithBearer: FetchFn = (input, init) => {
    const headers = new Headers(init?.headers);
    headers.set("Authorization", `Bearer ${connection.token}`);
    return globalThis.fetch(input, { ...init, headers });
  };

  return createClient({
    url: connection.endpoint,
    fetch: fetchWithBearer,
    preferGetMethod: false,
    // TODO(F5): Add the daemon SSE/WS subscription transport when it lands; v1 polls.
    exchanges: [cacheExchange, fetchExchange],
  });
}

