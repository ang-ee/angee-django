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
  type GraphQLInputObjectType,
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

/** Filter value shape a relation field's list filter input accepts. */
export type ModelRelationFilterMode = "lookup" | "id";

/** Relation filter contract derived from the model's SDL filter input. */
export interface ModelRelationFilterMetadata {
  field: string;
  mode: ModelRelationFilterMode;
  lookup?: string;
  aggregateKey?: string;
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
  relationFilter?: ModelRelationFilterMetadata;
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
  /** Fields accepted by the create input object. */
  createFields?: readonly string[];
  /** Required (non-null, no default) fields of the create input — for client-side validation. */
  requiredCreateFields?: readonly string[];
  /** Mutation field updating one record. */
  update?: string;
  /** Fields accepted by the update patch input object, excluding its id. */
  updateFields?: readonly string[];
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
    const filterInput = filterInputForType(schema, type);
    const groupKeyFields = groupKeyFieldsForType(schema, type);
    const fields = Object.fromEntries(
      Object.values(type.getFields()).map((field) => {
        const metadata = metadataForField(field.name, field.type, field.description);
        const relationFilter = metadata.kind === "relation"
          ? relationFilterForField(field.name, filterInput, groupKeyFields)
          : undefined;
        return [
          field.name,
          {
            ...metadata,
            ...(relationFilter ? { relationFilter } : {}),
          },
        ];
      }),
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
        const createInput = modelInputType(field, type, "Input");
        if (rootFields.create === undefined && createInput) {
          rootFields.create = name;
          rootFields.createFields = inputFieldNames(createInput);
          rootFields.requiredCreateFields = requiredInputFields(createInput);
        }
        const updateInput = modelInputType(field, type, "Patch");
        if (rootFields.update === undefined && updateInput) {
          rootFields.update = name;
          rootFields.updateFields = inputFieldNames(updateInput).filter((field) => field !== "id");
        }
      }
      if (returnsNamedType(field.type, "DeletePreview") && hasArgument(field, "id")) {
        deleteCandidates.push(name);
      }
    }
    rootFields.delete = deleteFieldFor(type, deleteCandidates);
  }
  return rootFields;
}

function filterInputForType(
  schema: GraphQLSchema,
  type: GraphQLObjectType,
): GraphQLInputObjectType | null {
  const query = schema.getQueryType();
  if (!query) return null;
  for (const field of Object.values(query.getFields())) {
    if (!returnsCollectionOf(field.type, type.name)) continue;
    const input = modelInputType(field, type, "Filter");
    if (input) return input;
  }
  return null;
}

function groupKeyFieldsForType(
  schema: GraphQLSchema,
  type: GraphQLObjectType,
): ReadonlySet<string> {
  const query = schema.getQueryType();
  if (!query) return new Set();
  for (const field of Object.values(query.getFields())) {
    if (!groupByFieldMetadata(field, type)) continue;
    const returnType = namedObjectType(field.type);
    const resultsType = returnType?.getFields().results?.type;
    const rowType = resultsType ? listItemObjectType(resultsType) : null;
    const key = rowType?.getFields().key;
    const keyType = key ? namedObjectType(key.type) : null;
    if (keyType) return new Set(Object.keys(keyType.getFields()));
  }
  return new Set();
}

function relationFilterForField(
  fieldName: string,
  filterInput: GraphQLInputObjectType | null,
  groupKeyFields: ReadonlySet<string>,
): ModelRelationFilterMetadata | undefined {
  const filterFields = filterInput?.getFields();
  if (!filterFields) return undefined;
  const aggregateKey = relationAggregateKey(fieldName, groupKeyFields);
  for (const name of [fieldName, `${fieldName}Id`]) {
    const filterField = filterFields[name];
    const shape = filterField ? relationFilterShape(filterField.type) : null;
    if (shape) {
      return {
        field: name,
        ...shape,
        ...(aggregateKey ? { aggregateKey } : {}),
      };
    }
  }
  return undefined;
}

function relationAggregateKey(
  fieldName: string,
  groupKeyFields: ReadonlySet<string>,
): string | undefined {
  const idKey = `${fieldName}Id`;
  if (groupKeyFields.has(idKey)) return idKey;
  return groupKeyFields.has(fieldName) ? fieldName : undefined;
}

function relationFilterShape(
  type: GraphQLType,
): Pick<ModelRelationFilterMetadata, "mode" | "lookup"> | null {
  const namedType = getNamedType(type);
  if (isScalarType(namedType) && namedType.name === "ID") return { mode: "id" };
  if (!isInputObjectType(namedType)) return null;
  const fields = namedType.getFields();
  for (const lookup of ["sqid", "exact", "pk", "inList"]) {
    if (lookup in fields) return { mode: "lookup", lookup };
  }
  return null;
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
  return modelInputType(field, type, suffix) !== null;
}

function modelInputType(
  field: GraphQLField<unknown, unknown>,
  type: GraphQLObjectType,
  suffix: string,
): GraphQLInputObjectType | null {
  const inputName = `${inputBaseName(type)}${suffix}`;
  const arg = field.args.find((candidate) => getNamedType(candidate.type).name === inputName);
  const inputType = arg ? getNamedType(arg.type) : null;
  return inputType && isInputObjectType(inputType) ? inputType : null;
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
  inputType: GraphQLInputObjectType,
): readonly string[] {
  return Object.values(inputType.getFields())
    .filter((inputField) => isNonNullType(inputField.type) && inputField.defaultValue === undefined)
    .map((inputField) => inputField.name);
}

function inputFieldNames(inputType: GraphQLInputObjectType): readonly string[] {
  return Object.keys(inputType.getFields());
}

function deleteFieldFor(type: GraphQLObjectType, candidates: readonly string[]): string | undefined {
  const target = inputBaseName(type);
  return candidates.find((candidate) => namesMatchSuffix(candidate, target));
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
