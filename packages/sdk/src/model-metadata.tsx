import { createElement, useMemo, type ReactNode } from "react";
import {
  buildSchema,
  getNamedType,
  isEnumType,
  isInputObjectType,
  isInterfaceType,
  isListType,
  isNonNullType,
  isObjectType,
  isScalarType,
  type GraphQLField,
  type GraphQLNamedType,
  type GraphQLObjectType,
  type GraphQLOutputType,
  type GraphQLSchema,
  type GraphQLType,
} from "graphql";

import { makeContext } from "./make-context";
import { schemaObjectTypes } from "./schema-object-types";
import { typeNameForModel } from "./selection";

/** Field shape classes the SDL can expose to rendered bindings. */
export type ModelFieldKind = "scalar" | "enum" | "relation" | "list";

/**
 * One GraphQL enum value plus its SDL-authored description, if any. The SDK
 * stays structural: it carries the raw value and the SDL description; the
 * rendered binding humanizes a description-less value into a display label.
 */
export interface ModelEnumValueMetadata {
  value: string;
  description?: string;
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

/** Root operation fields the SDL declares for one exposed model type. */
export interface ModelRootFieldMetadata {
  /** Query field returning one record by id. */
  detail?: string;
  /** Query field returning the model's list, connection, or page envelope. */
  list?: string;
  /** Query field returning the model's aggregate bucket. */
  aggregate?: string;
  /** Query field returning grouped aggregate buckets. */
  groupBy?: string;
  /** Input type accepted by the grouped aggregate root's `groupBy` argument. */
  groupByInput?: string;
  /** Input type accepted by the grouped aggregate root's `orderBy` argument. */
  groupOrderInput?: string;
  /** Query field returning newest-first field revisions for one record. */
  revisions?: string;
  /** Selectable fields on the revision projection type, excluding `id`. */
  revisionFields?: readonly string[];
  /** Mutation field creating one record. */
  create?: string;
  /** Required (non-null, no default) fields of the create input — for client-side validation. */
  requiredCreateFields?: readonly string[];
  /** Mutation field updating one record. */
  update?: string;
  /** Mutation field deleting one record. */
  delete?: string;
}

/** Metadata for one GraphQL object type. */
export interface ModelMetadata {
  typeName: string;
  fields: Readonly<Record<string, ModelFieldMetadata>>;
  /** Schema-declared root operation fields that address this model type. */
  rootFields?: ModelRootFieldMetadata;
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
 * Parse one printed GraphQL SDL string into object-field metadata. Enum values
 * carry their SDL description (the authored label) where present; the rendered
 * binding humanizes a description-less value into a display label.
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
    () => (modelLabel ? modelMetadataForLabel(metadata, modelLabel) : null),
    [metadata, modelLabel],
  );
}

/** Return the active schema's full metadata map. */
export function useSchemaFieldMetadata(): SchemaFieldMetadata {
  return ModelMetadataContext.useMaybe() ?? EMPTY_SCHEMA_FIELD_METADATA;
}

/**
 * Schema-declared root fields for a model, or `null` when the active schema has
 * no SDL configured. The two cases are deliberately distinct:
 *
 * - **No SDL configured** (the metadata map is empty) — the hooks stay inert
 *   (no document, no fetch). This is the ui rendered without the data layer
 *   (isolated tests, storybook, a view mounted outside a data-wired shell), not
 *   an error.
 * - **SDL configured but the model is absent** — a real misconfiguration, so it
 *   fails loud rather than guessing a field name.
 */
export function useModelRootFields(
  modelLabel: string,
): ModelRootFieldMetadata | null {
  const metadata = useSchemaFieldMetadata();
  return useMemo(() => {
    if (!modelLabel) return null;
    if (Object.keys(metadata.types).length === 0) return null;
    const model = modelMetadataForLabel(metadata, modelLabel);
    if (!model?.rootFields) {
      throw new Error(
        `GraphQL schema is configured with SDL but exposes no root fields for ` +
          `model "${modelLabel}"; declare its Query/Mutation fields or correct ` +
          "the model label.",
      );
    }
    return model.rootFields;
  }, [metadata, modelLabel]);
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
  const rootFields = rootFieldsByType(schema);
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
      ...(rootFields[type.name] ? { rootFields: rootFields[type.name] } : {}),
      ...(recordRepresentation ? { recordRepresentation } : {}),
    };
  }
  return { types };
}

function rootFieldsByType(schema: GraphQLSchema): Record<string, ModelRootFieldMetadata> {
  const fields: Record<string, ModelRootFieldMetadata> = {};
  for (const type of schemaObjectTypes(schema)) {
    const rootFields = rootFieldsForType(schema, type);
    if (Object.values(rootFields).some((field) => field !== undefined)) {
      fields[type.name] = rootFields;
    }
  }
  return fields;
}

function rootFieldsForType(
  schema: GraphQLSchema,
  type: GraphQLObjectType,
): ModelRootFieldMetadata {
  const rootFields: ModelRootFieldMetadata = {};
  const query = schema.getQueryType();
  if (query) {
    for (const [name, field] of Object.entries(query.getFields())) {
      if (rootFields.detail === undefined && isDetailField(field, type)) {
        rootFields.detail = name;
      }
      if (rootFields.list === undefined && isListField(field, type)) {
        rootFields.list = name;
      }
      if (rootFields.aggregate === undefined && isAggregateField(field, type)) {
        rootFields.aggregate = name;
      }
      const groupBy = groupByFieldMetadata(field, type);
      if (rootFields.groupBy === undefined && groupBy) {
        rootFields.groupBy = name;
        rootFields.groupByInput = groupBy.groupByInput;
        if (groupBy.groupOrderInput) {
          rootFields.groupOrderInput = groupBy.groupOrderInput;
        }
      }
      const revision = revisionFieldMetadata(field, type);
      if (rootFields.revisions === undefined && revision) {
        rootFields.revisions = name;
        rootFields.revisionFields = revision.fields;
      }
    }
  }
  const mutation = schema.getMutationType();
  if (mutation) {
    const deleteCandidates: string[] = [];
    for (const [name, field] of Object.entries(mutation.getFields())) {
      if (returnsDirectObject(field.type, type.name)) {
        if (rootFields.create === undefined && hasModelInputArg(field, type, "Input")) {
          rootFields.create = name;
          rootFields.requiredCreateFields = requiredInputFields(field, type, "Input");
        }
        if (rootFields.update === undefined && hasModelInputArg(field, type, "Patch")) {
          rootFields.update = name;
        }
      }
      if (returnsNamedType(field.type, "DeletePreview") && hasArgument(field, "id")) {
        deleteCandidates.push(name);
      }
    }
    rootFields.delete = deleteFieldFor(type, rootFields, deleteCandidates);
  }
  return rootFields;
}

function isDetailField(
  field: GraphQLField<unknown, unknown>,
  type: GraphQLObjectType,
): boolean {
  return returnsDirectObject(field.type, type.name) && hasArgument(field, "id");
}

function isListField(
  field: GraphQLField<unknown, unknown>,
  type: GraphQLObjectType,
): boolean {
  return returnsCollectionOf(field.type, type.name);
}

function isAggregateField(
  field: GraphQLField<unknown, unknown>,
  type: GraphQLObjectType,
): boolean {
  const returnType = namedObjectType(field.type);
  if (!returnType) return false;
  const fields = returnType.getFields();
  return "count" in fields
    && !("results" in fields)
    && hasModelInputArg(field, type, "Filter");
}

interface GroupByFieldMetadata {
  groupByInput: string;
  groupOrderInput?: string;
}

function groupByFieldMetadata(
  field: GraphQLField<unknown, unknown>,
  type: GraphQLObjectType,
): GroupByFieldMetadata | null {
  const returnType = namedObjectType(field.type);
  const groupBySpec = groupBySpecArgName(field);
  if (!returnType || !groupBySpec || !groupBySpecMatchesType(groupBySpec, type)) {
    return null;
  }
  const resultsType = returnType.getFields().results?.type;
  const rowType = resultsType ? listItemObjectType(resultsType) : null;
  if (!rowType) return null;
  const rowFields = rowType.getFields();
  if (!("key" in rowFields && "count" in rowFields)) return null;
  const groupOrderInput = groupOrderArgName(field);
  return {
    groupByInput: groupBySpec,
    ...(groupOrderInput ? { groupOrderInput } : {}),
  };
}

function groupBySpecArgName(
  field: GraphQLField<unknown, unknown>,
): string | null {
  const arg = field.args.find((candidate) => candidate.name === "groupBy");
  if (!arg) return null;
  const inputType = getNamedType(arg.type);
  if (!isInputObjectType(inputType)) return null;
  const name = inputType.name;
  return name.endsWith("GroupBySpec") ? name : null;
}

function groupOrderArgName(
  field: GraphQLField<unknown, unknown>,
): string | null {
  const arg = field.args.find((candidate) => candidate.name === "orderBy");
  if (!arg) return null;
  const inputType = getNamedType(arg.type);
  return isInputObjectType(inputType) ? inputType.name : null;
}

function groupBySpecMatchesType(
  groupBySpecName: string,
  type: GraphQLObjectType,
): boolean {
  const base = inputBaseName(type);
  return groupBySpecName === `${base}GroupBySpec`
    || groupBySpecName.startsWith(`${base}Aggregate`);
}

function revisionFieldMetadata(
  field: GraphQLField<unknown, unknown>,
  type: GraphQLObjectType,
): { fields: readonly string[] } | null {
  if (!hasArgument(field, "id")) return null;
  const revisionType = listItemObjectType(field.type);
  if (!revisionType || revisionType.name !== `${inputBaseName(type)}Revision`) {
    return null;
  }
  return {
    fields: Object.keys(revisionType.getFields()).filter((name) => name !== "id"),
  };
}

function returnsDirectObject(type: GraphQLOutputType, name: string): boolean {
  if (containsList(type)) return false;
  return returnsNamedType(type, name);
}

function returnsNamedType(type: GraphQLType, name: string): boolean {
  return getNamedType(type).name === name;
}

function returnsCollectionOf(type: GraphQLOutputType, name: string): boolean {
  const itemType = listItemNamedType(type);
  if (itemType?.name === name) return true;
  const objectType = namedObjectType(type);
  if (!objectType) return false;
  const fields = objectType.getFields();
  if (fields.results && listItemNamedType(fields.results.type)?.name === name) {
    return true;
  }
  if (fields.nodes && listItemNamedType(fields.nodes.type)?.name === name) {
    return true;
  }
  const edgeType = fields.edges ? listItemObjectType(fields.edges.type) : null;
  const node = edgeType?.getFields().node;
  return node ? returnsNamedType(node.type, name) : false;
}

function hasArgument(field: GraphQLField<unknown, unknown>, name: string): boolean {
  return field.args.some((arg) => arg.name === name);
}

function hasModelInputArg(
  field: GraphQLField<unknown, unknown>,
  type: GraphQLObjectType,
  suffix: string,
): boolean {
  const inputName = `${inputBaseName(type)}${suffix}`;
  return field.args.some((arg) => getNamedType(arg.type).name === inputName);
}

function inputBaseName(type: GraphQLObjectType): string {
  return type.name.endsWith("Type") ? type.name.slice(0, -4) : type.name;
}

/**
 * Names of the create input's required fields — non-null with no server default.
 * A field with a default (or a nullable one) is optional, so the client must not
 * block submit on it. Used for client-side required validation.
 */
function requiredInputFields(
  field: GraphQLField<unknown, unknown>,
  type: GraphQLObjectType,
  suffix: string,
): readonly string[] {
  const inputName = `${inputBaseName(type)}${suffix}`;
  const arg = field.args.find((candidate) => getNamedType(candidate.type).name === inputName);
  const inputType = arg ? getNamedType(arg.type) : null;
  if (!inputType || !isInputObjectType(inputType)) return [];
  return Object.values(inputType.getFields())
    .filter((inputField) => isNonNullType(inputField.type) && inputField.defaultValue === undefined)
    .map((inputField) => inputField.name);
}

function deleteFieldFor(
  type: GraphQLObjectType,
  rootFields: ModelRootFieldMetadata,
  candidates: readonly string[],
): string | undefined {
  // DeletePreview does not carry the deleted model type in SDL, so the schema
  // cannot link delete to a model by return type the way create/update can.
  const suffixes = [
    commonSuffix(rootFields.create, rootFields.update),
    inputBaseName(type),
  ].filter((suffix): suffix is string => Boolean(suffix));
  return candidates.find((candidate) =>
    suffixes.some((suffix) => namesMatchSuffix(candidate, suffix)),
  );
}

function commonSuffix(
  left: string | undefined,
  right: string | undefined,
): string | undefined {
  if (!left || !right) return undefined;
  let index = 0;
  while (
    index < left.length
    && index < right.length
    && left[left.length - 1 - index] === right[right.length - 1 - index]
  ) {
    index += 1;
  }
  return index > 0 ? left.slice(left.length - index) : undefined;
}

function namesMatchSuffix(name: string, suffix: string): boolean {
  return name.toLowerCase().endsWith(suffix.toLowerCase());
}

function namedObjectType(type: GraphQLOutputType): GraphQLObjectType | null {
  const namedType = getNamedType(type);
  return isObjectType(namedType) ? namedType : null;
}

function listItemObjectType(type: GraphQLOutputType): GraphQLObjectType | null {
  const namedType = listItemNamedType(type);
  return namedType && isObjectType(namedType) ? namedType : null;
}

function listItemNamedType(type: GraphQLType): GraphQLNamedType | null {
  const unwrapped = unwrapNonNull(type);
  return isListType(unwrapped) ? getNamedType(unwrapped.ofType) : null;
}

function containsList(type: GraphQLType): boolean {
  const unwrapped = unwrapNonNull(type);
  return isListType(unwrapped)
    || (isNonNullType(type) && containsList(type.ofType));
}

function unwrapNonNull(type: GraphQLType): GraphQLType {
  return isNonNullType(type) ? unwrapNonNull(type.ofType) : type;
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
        ...(value.description?.trim()
          ? { description: value.description.trim() }
          : {}),
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
