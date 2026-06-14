import { createElement, useCallback, useMemo, useState, type ReactNode } from "react";
import { buildSchema, type GraphQLSchema } from "graphql";
import { Provider as UrqlProvider } from "urql";
import type { Client } from "@urql/core";

import { createUrqlClient, type AngeeUrqlClientOptions } from "./graphql-client";
import { cacheConfigFromSchema } from "./cache-config";
import { makeContext } from "./make-context";
import {
  EMPTY_SCHEMA_FIELD_METADATA,
  ModelMetadataProvider,
  fieldMetadataFromSchema,
  type SchemaFieldMetadata,
} from "./model-metadata";

interface SchemaRuntime {
  clients: Record<string, Client>;
  metadata: Record<string, SchemaFieldMetadata>;
}

function createSchemaRuntime(
  config: Record<string, AngeeUrqlClientOptions>,
): SchemaRuntime {
  const clients: Record<string, Client> = {};
  const metadata: Record<string, SchemaFieldMetadata> = {};
  for (const [name, options] of Object.entries(config)) {
    const schema = options.sdl ? buildSchema(options.sdl) : null;
    clients[name] = createUrqlClient(optionsWithSchemaDefaults(options, schema));
    if (schema) metadata[name] = fieldMetadataFromSchema(schema);
  }
  return { clients, metadata };
}

function optionsWithSchemaDefaults(
  options: AngeeUrqlClientOptions,
  schema: GraphQLSchema | null | undefined = undefined,
): AngeeUrqlClientOptions {
  const resolvedSchema = schema === undefined && options.sdl && !options.cache
    ? buildSchema(options.sdl)
    : schema;
  if (!resolvedSchema || options.cache) return options;
  return {
    ...options,
    cache: cacheConfigFromSchema(resolvedSchema),
  };
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
  const metadata =
    SchemaMetadataContext.useMaybe()?.[props.schema] ?? EMPTY_SCHEMA_FIELD_METADATA;
  if (!client) {
    const known = Object.keys(props.clients).join(", ") || "none";
    throw new Error(
      `No GraphQL client for schema "${props.schema}"; configured schemas: ${known}.`,
    );
  }
  return createElement(ModelMetadataProvider, {
    metadata,
    children: createElement(UrqlProvider, { value: client }, props.children),
  });
}

const ResetContext = makeContext<() => void>("GraphQLClientReset");
const ClientsContext = makeContext<Record<string, Client>>("GraphQLClients");
const SchemaMetadataContext =
  makeContext<Record<string, SchemaFieldMetadata>>("GraphQLSchemaMetadata");

const NO_RESET = (): void => {};
const NO_CLIENTS: Record<string, Client> = {};

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
  const runtime = useMemo(
    () => createSchemaRuntime(config),
    // `generation` participates so a reset rebuilds the pool from scratch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config, generation],
  );
  const { clients, metadata: schemaMetadata } = runtime;
  const reset = useCallback(() => setGeneration((current) => current + 1), []);
  return ResetContext.Provider({
    value: reset,
    children: ClientsContext.Provider({
      value: clients,
      children: SchemaMetadataContext.Provider({
        value: schemaMetadata,
        children: createElement(GraphQLProvider, {
          clients,
          schema: props.schema,
          children: props.children,
        }),
      }),
    }),
  });
}

/** The client reset for the surrounding pool, or a no-op when unprovided. */
export function useResetClient(): () => void {
  return ResetContext.useMaybe() ?? NO_RESET;
}

/**
 * The full per-schema client pool the surrounding `GraphQLClientProvider` owns,
 * rebuilt on reset. Lets a subtree reach a non-active schema's client — e.g. the
 * console client that carries the change subscriptions — while the app's reads
 * run on the active (public) schema. Empty when unprovided.
 */
export function useSchemaClients(): Record<string, Client> {
  return ClientsContext.useMaybe() ?? NO_CLIENTS;
}
