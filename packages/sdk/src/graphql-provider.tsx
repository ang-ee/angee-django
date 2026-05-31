import { createElement, useCallback, useMemo, useState, type ReactNode } from "react";
import { Provider as UrqlProvider } from "urql";
import type { Client } from "@urql/core";

import { createUrqlClient, type AngeeUrqlClientOptions } from "./graphql-client";
import { makeContext } from "./make-context";

/** One client per named schema, built once from the per-schema endpoint config. */
export function createSchemaClients(
  config: Record<string, AngeeUrqlClientOptions>,
): Record<string, Client> {
  const clients: Record<string, Client> = {};
  for (const [name, options] of Object.entries(config)) {
    clients[name] = createUrqlClient(options);
  }
  return clients;
}

/**
 * Bind a subtree to one named schema's client. A shell renders this with the
 * schema it targets, so call sites inside it inherit that schema's client
 * through urql's context and never name an endpoint.
 */
export function GraphQLProvider(props: {
  clients: Record<string, Client>;
  schema: string;
  children: ReactNode;
}): ReactNode {
  const client = props.clients[props.schema];
  if (!client) {
    const known = Object.keys(props.clients).join(", ") || "none";
    throw new Error(
      `No GraphQL client for schema "${props.schema}"; configured schemas: ${known}.`,
    );
  }
  return createElement(UrqlProvider, { value: client }, props.children);
}

const ResetContext = makeContext<() => void>("GraphQLClientReset");

const NO_RESET = (): void => {};

/**
 * Own the client lifecycle for a subtree and expose a reset. Reset rebuilds
 * every client, which drops the normalized cache and each client's CSRF token
 * in one step — the discard an auth change (login / logout) needs. Pass a
 * stable `config`; only a reset rebuilds the clients.
 */
export function GraphQLClientProvider(props: {
  config: Record<string, AngeeUrqlClientOptions>;
  schema: string;
  children: ReactNode;
}): ReactNode {
  const { config } = props;
  const [generation, setGeneration] = useState(0);
  const clients = useMemo(
    () => createSchemaClients(config),
    // `generation` participates so a reset rebuilds the pool from scratch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config, generation],
  );
  const reset = useCallback(() => setGeneration((current) => current + 1), []);
  return ResetContext.Provider({
    value: reset,
    children: createElement(GraphQLProvider, {
      clients,
      schema: props.schema,
      children: props.children,
    }),
  });
}

/** The client reset for the surrounding pool, or a no-op when unprovided. */
export function useResetClient(): () => void {
  return ResetContext.useMaybe() ?? NO_RESET;
}
