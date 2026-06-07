import { createElement, useMemo, type ReactNode } from "react";
import {
  buildSchema,
  getNamedType,
  isEnumType,
  isInterfaceType,
  isListType,
  isNonNullType,
  isObjectType,
  isScalarType,
  type GraphQLNamedType,
  type GraphQLSchema,
  type GraphQLType,
} from "graphql";

import { makeContext } from "./make-context";
import { schemaObjectTypes } from "./schema-object-types";
import { typeNameForModel } from "./selection";

/** Field shape classes the SDL can expose to rendered bindings. */
export type ModelFieldKind = "scalar" | "enum" | "relation" | "list";

/** One GraphQL enum value plus its human label. */
export interface ModelEnumValueMetadata {
  value: string;
  label: string;
}

/** Metadata for one GraphQL object field, derived from the printed SDL. */
export interface ModelFieldMetadata {
  name: string;
  label?: string;
  kind: ModelFieldKind;
  scalar?: string;
  enumName?: string;
  values?: readonly ModelEnumValueMetadata[];
  relationTarget?: string;
}

/** Metadata for one GraphQL object type. */
export interface ModelMetadata {
  typeName: string;
  fields: Readonly<Record<string, ModelFieldMetadata>>;
  /**
   * Inferred display field for records. Candidate order is title, name,
   * displayName, label, username, email, slug, then the first String scalar.
   */
  recordRepresentation?: string;
}

/** Per-type field metadata parsed from one schema SDL. */
export interface SchemaFieldMetadata {
  types: Readonly<Record<string, ModelMetadata>>;
}

/** Empty metadata used when a schema is configured without SDL. */
export const EMPTY_SCHEMA_FIELD_METADATA: SchemaFieldMetadata = { types: {} };

const ModelMetadataContext = makeContext<SchemaFieldMetadata>("ModelMetadata");

/**
 * Parse one printed GraphQL SDL string into object-field metadata. Enum value
 * labels come from enum-value descriptions and fall back to humanized enum
 * names when descriptions are absent.
 */
export function fieldMetadataFromSDL(sdl: string): SchemaFieldMetadata {
  return fieldMetadataFromSchema(buildSchema(sdl));
}

/**
 * Provide the active schema's metadata to rendered bindings. Hosts normally get
 * this automatically through `GraphQLClientProvider` when their schema config
 * carries `sdl`.
 */
export function ModelMetadataProvider({
  metadata = EMPTY_SCHEMA_FIELD_METADATA,
  children,
}: {
  metadata?: SchemaFieldMetadata;
  children: ReactNode;
}): ReactNode {
  return createElement(ModelMetadataContext.Provider, {
    value: metadata,
    children,
  });
}

/** Return metadata for a Django model label in the active GraphQL schema. */
export function useModelMetadata(modelLabel: string): ModelMetadata | null {
  const metadata = useSchemaFieldMetadata();
  return useMemo(
    () => modelMetadataForLabel(metadata, modelLabel),
    [metadata, modelLabel],
  );
}

/** Return the active schema's full metadata map. */
export function useSchemaFieldMetadata(): SchemaFieldMetadata {
  return ModelMetadataContext.useMaybe() ?? EMPTY_SCHEMA_FIELD_METADATA;
}

/** Resolve a Django model label such as `notes.Note` to its GraphQL type metadata. */
export function modelMetadataForLabel(
  metadata: SchemaFieldMetadata,
  modelLabel: string,
): ModelMetadata | null {
  const typeName = typeNameForModel(modelLabel);
  return metadata.types[`${typeName}Type`] ?? metadata.types[typeName] ?? null;
}

/** Derive object-field metadata from a built GraphQL schema. */
export function fieldMetadataFromSchema(schema: GraphQLSchema): SchemaFieldMetadata {
  const types: Record<string, ModelMetadata> = {};
  for (const type of schemaObjectTypes(schema)) {
    const fields = Object.fromEntries(
      Object.values(type.getFields()).map((field) => [
        field.name,
        metadataForField(field.name, field.type, field.description),
      ]),
    );
    const recordRepresentation = recordRepresentationFor(fields);
    types[type.name] = {
      typeName: type.name,
      fields,
      ...(recordRepresentation ? { recordRepresentation } : {}),
    };
  }
  return { types };
}

function metadataForField(
  name: string,
  type: GraphQLType,
  description: string | null | undefined,
): ModelFieldMetadata {
  return {
    name,
    ...metadataForNamedType(getNamedType(type), hasList(type)),
    ...(description && description.trim() ? { label: description.trim() } : {}),
  };
}

function metadataForNamedType(
  namedType: GraphQLNamedType,
  list: boolean,
): Omit<ModelFieldMetadata, "name" | "label"> {
  const kind = list ? "list" : undefined;
  if (isScalarType(namedType)) {
    return {
      kind: kind ?? "scalar",
      scalar: namedType.name,
    };
  }
  if (isEnumType(namedType)) {
    return {
      kind: kind ?? "enum",
      enumName: namedType.name,
      values: namedType.getValues().map((value) => ({
        value: value.name,
        label: enumValueLabel(value.name, value.description),
      })),
    };
  }
  if (isObjectType(namedType) || isInterfaceType(namedType)) {
    return {
      kind: kind ?? "relation",
      relationTarget: namedType.name,
    };
  }
  return {
    kind: kind ?? "scalar",
    scalar: namedType.name,
  };
}

function hasList(type: GraphQLType): boolean {
  if (isNonNullType(type)) return hasList(type.ofType);
  return isListType(type);
}

function enumValueLabel(
  value: string,
  description: string | null | undefined,
): string {
  const label = description?.trim();
  if (label) return label;
  return value
    .toLowerCase()
    .replace(/[-_.\s]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

/**
 * Return the inferred display field for records. Candidate order is title,
 * name, displayName, label, username, email, slug, then the first String scalar.
 */
function recordRepresentationFor(
  fields: Readonly<Record<string, ModelFieldMetadata>>,
): string | undefined {
  const candidates = [
    "title",
    "name",
    "displayName",
    "label",
    "username",
    "email",
    "slug",
  ];
  for (const candidate of candidates) {
    if (isDisplayScalar(fields[candidate])) return candidate;
  }
  return Object.values(fields).find(isDisplayScalar)?.name;
}

function isDisplayScalar(field: ModelFieldMetadata | undefined): boolean {
  return field?.kind === "scalar" && field.scalar === "String";
}
