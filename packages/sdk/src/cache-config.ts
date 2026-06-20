import {
  buildSchema,
  getNamedType,
  type GraphQLObjectType,
  type GraphQLSchema,
} from "graphql";
import { relayPagination } from "@urql/exchange-graphcache/extras";
import type { KeyingConfig, ResolverConfig } from "@urql/exchange-graphcache";

import { schemaObjectTypes } from "./schema-object-types";

/**
 * The graphcache keying + resolver configuration, derived from the schema so it
 * stays correct as the schema grows — no hand-maintained type list. An entity
 * (any object type exposing an `id`) is normalized by its public id; every other
 * object type is a value object and is null-keyed. Every query field returning a
 * Relay-style `*Connection` gets a pagination resolver so cursor pages merge into
 * one list; offset-paginated fields need none — each page is cached by its variables.
 */
export interface CacheConfig {
  keys: KeyingConfig;
  resolvers: ResolverConfig;
}

function hasIdField(fields: Record<string, unknown>): boolean {
  return "id" in fields;
}

function isPublicNode(type: GraphQLObjectType): boolean {
  return hasIdField(type.getFields())
    && type.getInterfaces().some((iface) => iface.name === "Node");
}

export function cacheConfigFromSchema(schema: GraphQLSchema): CacheConfig {
  const keys: KeyingConfig = {};
  for (const type of schemaObjectTypes(schema)) {
    keys[type.name] = isPublicNode(type)
      ? (data) => (typeof data.id === "string" ? data.id : null)
      : () => null;
  }

  const resolvers: ResolverConfig = {};
  const queryType = schema.getQueryType();
  if (queryType) {
    const queryResolvers: Record<string, ReturnType<typeof relayPagination>> = {};
    for (const [fieldName, field] of Object.entries(queryType.getFields())) {
      if (getNamedType(field.type).name.endsWith("Connection")) {
        queryResolvers[fieldName] = relayPagination();
      }
    }
    if (Object.keys(queryResolvers).length > 0) resolvers.Query = queryResolvers;
  }

  return { keys, resolvers };
}

/** Build the cache config straight from a printed SDL string (the runtime
 * `schemas/<name>.graphql` an app imports). Saves the caller a `graphql`
 * dependency just to parse the schema. */
export function cacheConfigFromSDL(sdl: string): CacheConfig {
  return cacheConfigFromSchema(buildSchema(sdl));
}
