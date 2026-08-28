import { createContext, useContext } from "react";
import { graphQLWebSocketUrl, isFatalGraphQLWsClose } from "@angee/refine";
import { createClient as createWSClient, type Client } from "graphql-ws";

import type { OperatorConnectionInfo } from "./types";

/** The daemon graphql-ws client; carries the live subscriptions (snapshot, logs, status). */
type OperatorWsClient = Client;

/**
 * Build the daemon's graphql-ws client. Request/response rides the Refine
 * `operator` data provider (`operator-provider.ts`); this client carries only the
 * live subscriptions. The bearer is captured in the socket's `connectionParams`,
 * so a token rotation rebuilds the client (the gate keys it on the token) —
 * graphql-ws cannot swap connection params on a live socket.
 */
export function createOperatorClient(
  connection: OperatorConnectionInfo,
): OperatorWsClient {
  return createWSClient({
    url: graphQLWebSocketUrl(connection.endpoint),
    connectionParams: { authorization: `Bearer ${connection.token}` },
    lazy: true,
    shouldRetry: (event: unknown) => !isFatalGraphQLWsClose(event),
  });
}

const OperatorWsClientContext = createContext<OperatorWsClient | null>(null);

/** Provide the daemon ws client to the subscription hooks (mounted by the gate). */
export const OperatorWsClientProvider = OperatorWsClientContext.Provider;

/** The daemon ws client for subscription hooks; throws outside the transport gate. */
export function useOperatorWsClient(): OperatorWsClient {
  const client = useContext(OperatorWsClientContext);
  if (!client) {
    throw new Error(
      "useOperatorWsClient must be used inside OperatorTransportProvider.",
    );
  }
  return client;
}
