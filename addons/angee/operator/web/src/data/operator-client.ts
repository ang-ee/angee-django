import {
  bearerAuth,
  createUrqlClient,
  graphQLWebSocketUrl,
  isFatalGraphQLWsClose,
} from "@angee/sdk";
import { createClient as createWSClient } from "graphql-ws";
import {
  cacheExchange,
  fetchExchange,
  subscriptionExchange,
  type Client,
  type ExecutionResult,
  type SubscriptionForwarder,
} from "urql";

import type { OperatorConnectionInfo } from "./types";

export function createOperatorClient(connection: OperatorConnectionInfo): Client {
  // The SDK client factory owns transport and HTTP auth: bearerAuth carries the
  // minted token on every fetch. The operator contributes a document cache and a
  // graphql-ws subscription transport (bearer in connectionParams) reserved for
  // future daemon streaming — today every operator op is a query/mutation over
  // fetch. Canonical urql order: cache, subscription (forwards downstream), fetch.
  return createUrqlClient({
    url: connection.endpoint,
    auth: bearerAuth(connection.token),
    exchanges: [
      cacheExchange,
      subscriptionExchange({
        forwardSubscription: subscriptionForwarder(connection),
      }),
      fetchExchange,
    ],
  });
}

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
    shouldRetry: (event: unknown) => !isFatalGraphQLWsClose(event),
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
