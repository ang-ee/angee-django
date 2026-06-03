import { graphQLWebSocketUrl } from "@angee/sdk";
import { createClient as createWSClient } from "graphql-ws";
import {
  cacheExchange,
  createClient,
  subscriptionExchange,
  type Client,
  type ExecutionResult,
  type SubscriptionForwarder,
} from "urql";

import type { OperatorConnectionInfo } from "./types";

export function createOperatorClient(connection: OperatorConnectionInfo): Client {
  return createClient({
    url: connection.endpoint,
    preferGetMethod: false,
    exchanges: [
      cacheExchange,
      subscriptionExchange({
        enableAllOperations: true,
        forwardSubscription: subscriptionForwarder(connection),
      }),
    ],
  });
}

const FATAL_WS_CLOSE_CODES = new Set([1000, 1008, 4400, 4401, 4403, 4406, 4409]);

function subscriptionForwarder(
  connection: OperatorConnectionInfo,
): SubscriptionForwarder {
  if (typeof WebSocket === "undefined") {
    return () => ({ subscribe: () => ({ unsubscribe() {} }) });
  }
  const wsClient = createWSClient({
    url: graphQLWebSocketUrl(connection.endpoint),
    connectionParams: { authorization: `Bearer ${connection.token}` },
    lazy: true,
    shouldRetry: (event: unknown) =>
      !(event instanceof CloseEvent && FATAL_WS_CLOSE_CODES.has(event.code)),
  });
  return (request) => ({
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
