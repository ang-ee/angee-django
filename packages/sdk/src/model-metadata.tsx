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
  type GraphQLInputObjectType,
  type GraphQLNamedType,
  type GraphQLSchema,
  type GraphQLType,
} from "graphql";

import { makeContext } from "./make-context";
import { schemaObjectTypes } from "./schema-object-types";
import { typeNameForModel } from "./selection";

/** Field shape classes the SDL can expose to rendered bindings. */
export type ModelFieldKind = "scalar" | "enum" | "relation" | "list";

const MODEL_FIELD_KINDS = new Set<ModelFieldKind>([
  "scalar",
  "enum",
  "relation",
  "list",
]);

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
  /** Group-key field carrying the related record's display label, when the model
   * registers a `<relation>__<label>` group axis (e.g. `party_DisplayName`). Lets
   * a relation group show the contact's name, not its id (Odoo's read_group). */
  labelKey?: string;
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
  filterable?: boolean;
  sortable?: boolean;
  aggregatable?: boolean;
  groupable?: boolean;
  readable?: boolean;
  creatable?: boolean;
  updatable?: boolean;
  requiredOnCreate?: boolean;
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
  /** Authored mutation field returning a cascade delete preview. */
  deletePreview?: string;
  /** Subscription field emitting change notifications for one model. */
  changes?: string;
}

/** Generated schema metadata emitted beside SDL by Angee's backend schema owner. */
export interface AngeeSchemaMetadata {
  angee?: {
    resources?: readonly DataResourceMetadata[];
  };
}

/**
 * Validate and narrow generated schema metadata loaded from JSON. TypeScript
 * widens JSON string literals, while the runtime contract is owned by the
 * backend metadata emitter; this keeps that boundary explicit before callers
 * feed metadata into SDL-derived resource owners.
 */
export function defineAngeeSchemaMetadata(
  metadata: unknown,
): AngeeSchemaMetadata {
  const root = metadataObject(metadata, "schema metadata");
  const angee = optionalMetadataObject(root.angee, "schema metadata.angee");
  const resources = angee
    ? optionalMetadataArray(
      angee.resources,
      "schema metadata.angee.resources",
    )
    : undefined;
  resources?.forEach((resource, index) =>
    validateGeneratedResource(resource, `schema metadata.angee.resources[${index}]`),
  );
  return root as AngeeSchemaMetadata;
}

/** One model-backed data resource declared by the backend. */
export interface DataResourceMetadata {
  schemaName: string;
  modelLabel: string;
  appLabel: string;
  modelName: string;
  publicIdField: string;
  roots: DataResourceRootMetadata;
  typeNames: DataResourceTypeMetadata;
  capabilities: readonly string[];
  fields?: readonly DataResourceFieldMetadata[];
  filterFields: readonly string[];
  orderFields: readonly string[];
  aggregateFields: readonly string[];
  groupByFields: readonly string[];
  groupDimensions?: readonly DataResourceGroupDimensionMetadata[];
  aggregateMeasures?: readonly DataResourceAggregateMeasureMetadata[];
  defaultMeasures?: readonly DataResourceAggregateMeasureMetadata[];
  defaultSort?: readonly DataResourceDefaultSortMetadata[];
  createFields?: readonly string[];
  updateFields?: readonly string[];
  requiredCreateFields?: readonly string[];
  revisionFields?: readonly string[];
  relationAxes: readonly DataResourceRelationAxisMetadata[];
  groupAliases?: readonly DataResourceGroupAliasMetadata[];
}

/** GraphQL root field names emitted for one model data resource. */
export interface DataResourceRootMetadata {
  list?: string | null;
  detail?: string | null;
  aggregate?: string | null;
  groups?: string | null;
  create?: string | null;
  update?: string | null;
  delete?: string | null;
  deletePreview?: string | null;
  revisions?: string | null;
  changes?: string | null;
}

/** GraphQL type names owned or referenced by one data resource. */
export interface DataResourceTypeMetadata {
  query?: string | null;
  node?: string | null;
  filter?: string | null;
  order?: string | null;
  aggregate?: string | null;
  grouped?: string | null;
  groupKey?: string | null;
  groupBySpec?: string | null;
  groupOrder?: string | null;
  having?: string | null;
  createInput?: string | null;
  updateInput?: string | null;
  deletePayload?: string | null;
  revision?: string | null;
}

/** Backend-emitted field capability metadata for one resource field. */
export interface DataResourceFieldMetadata {
  name: string;
  kind: ModelFieldKind;
  scalar?: string | null;
  widget?: string | null;
  readable: boolean;
  filterable: boolean;
  sortable: boolean;
  aggregatable: boolean;
  groupable: boolean;
  creatable: boolean;
  updatable: boolean;
  requiredOnCreate: boolean;
  relationModelLabel?: string | null;
  relationLabelAxis?: string | null;
}

/** Relation axis metadata from the generated backend resource contract. */
export interface DataResourceRelationAxisMetadata {
  field: string;
  modelLabel: string;
  publicIdField: string;
  labelAxis?: string | null;
}

/** Display field that groups through another backend aggregate axis. */
export interface DataResourceGroupAliasMetadata {
  field: string;
  aggregateField: string;
  aggregateKey: string;
}

/** One extraction supported by a backend group dimension. */
export interface DataResourceGroupExtractionMetadata {
  name: string;
  input: string;
  key: string;
  rangeKey?: string | null;
}

/** Backend-owned group dimension metadata for grouped aggregate buckets. */
export interface DataResourceGroupDimensionMetadata {
  field: string;
  input: string;
  key: string;
  kind: "column" | "relation" | string;
  scalar?: string | null;
  extractions?: readonly DataResourceGroupExtractionMetadata[];
}

/** Aggregate measure selectable for one resource. */
export interface DataResourceAggregateMeasureMetadata {
  op: string;
  field?: string | null;
  input?: string | null;
}

/** Default resource ordering exposed by the backend order input. */
export interface DataResourceDefaultSortMetadata {
  field: string;
  direction: "ASC" | "DESC" | string;
}

/** Metadata for one GraphQL object type. */
export interface ModelMetadata {
  typeName: string;
  fields: Readonly<Record<string, ModelFieldMetadata>>;
  /** Schema-declared root operation fields that address this model type. */
  rootFields?: ModelRootFieldMetadata;
  /** Backend-declared model resource contract, when this type has one. */
  resource?: DataResourceMetadata;
  /**
   * Inferred display field for records. Candidate order is title, name,
   * displayName, label, username, email, slug, then the first String scalar.
   */
  recordRepresentation?: string;
}

/** Per-type field metadata parsed from one schema SDL. */
export interface SchemaFieldMetadata {
  types: Readonly<Record<string, ModelMetadata>>;
  resources?: readonly DataResourceMetadata[];
}

/** Empty metadata used when a schema is configured without SDL. */
export const EMPTY_SCHEMA_FIELD_METADATA: SchemaFieldMetadata = { types: {} };

const ModelMetadataContext = makeContext<SchemaFieldMetadata>("ModelMetadata");

/**
 * Parse one printed GraphQL SDL string into object-field metadata. Enum values
 * carry their SDL description (the authored label) where present; the rendered
 * binding humanizes a description-less value into a display label.
 */
export function fieldMetadataFromSDL(
  sdl: string,
  metadata?: AngeeSchemaMetadata,
): SchemaFieldMetadata {
  return fieldMetadataFromSchema(buildSchema(sdl), metadata);
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
export function useModelRootFields(modelLabel: string): ModelRootFieldMetadata | null;
export function useModelRootFields(
  modelLabel: string,
  options: { required: false },
): ModelRootFieldMetadata | null | undefined;
export function useModelRootFields(
  modelLabel: string,
  options: { required: boolean },
): ModelRootFieldMetadata | null | undefined;
export function useModelRootFields(
  modelLabel: string,
  options: { required?: boolean } = {},
): ModelRootFieldMetadata | null | undefined {
  const metadata = useSchemaFieldMetadata();
  return useMemo(() => {
    if (!modelLabel) return null;
    if (Object.keys(metadata.types).length === 0) return null;
    const model = modelMetadataForLabel(metadata, modelLabel);
    if (!model?.rootFields) {
      if (options.required === false) return undefined;
      throw new Error(
        `GraphQL schema is configured with SDL but exposes no resource metadata ` +
          `for model "${modelLabel}"; emit it in angee.resources or correct the ` +
          "model label.",
      );
    }
    return model.rootFields;
  }, [metadata, modelLabel, options.required]);
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
export function fieldMetadataFromSchema(
  schema: GraphQLSchema,
  metadata?: AngeeSchemaMetadata,
): SchemaFieldMetadata {
  const types: Record<string, ModelMetadata> = {};
  const resources = metadata?.angee?.resources ?? [];
  validateDataResourceMetadata(schema, resources);
  const resourcesByType = resourcesByNodeType(resources);
  for (const type of schemaObjectTypes(schema)) {
    const resource = resourcesByType[type.name];
    const filterInput = filterInputForResource(schema, resource);
    const groupKeyFields = groupKeyFieldsForResource(schema, resource);
    const relationAxes = resource ? relationAxesByField(resource) : {};
    const resourceFields = resourceFieldsByName(resource);
    const fields = Object.fromEntries(
      Object.values(type.getFields()).map((field) => {
        const metadata = metadataForField(field.name, field.type, field.description);
        const resourceField = resourceFields[field.name];
        const relationFilter = metadata.kind === "relation"
          ? relationFilterForField(
              field.name,
              filterInput,
              groupKeyFields,
              resource,
              relationAxes[field.name],
            )
          : undefined;
        return [
          field.name,
          {
            ...metadata,
            ...(resourceField ? fieldCapabilitiesFromResource(resourceField) : {}),
            ...(relationFilter ? { relationFilter } : {}),
          },
        ];
      }),
    );
    const recordRepresentation = recordRepresentationFor(fields);
    const modelRootFields = resource ? rootFieldsFromResource(resource) : undefined;
    types[type.name] = {
      typeName: type.name,
      fields,
      ...(modelRootFields ? { rootFields: modelRootFields } : {}),
      ...(resource ? { resource } : {}),
      ...(recordRepresentation ? { recordRepresentation } : {}),
    };
  }
  return {
    types,
    ...(resources.length > 0 ? { resources } : {}),
  };
}

function validateGeneratedResource(resource: unknown, path: string): void {
  const value = metadataObject(resource, path);
  for (const property of [
    "schemaName",
    "modelLabel",
    "appLabel",
    "modelName",
    "publicIdField",
  ]) {
    expectMetadataString(value[property], `${path}.${property}`);
  }
  validateStringRecord(metadataObject(value.roots, `${path}.roots`), `${path}.roots`);
  validateStringRecord(
    metadataObject(value.typeNames, `${path}.typeNames`),
    `${path}.typeNames`,
  );
  for (const property of [
    "capabilities",
    "filterFields",
    "orderFields",
    "aggregateFields",
    "groupByFields",
  ]) {
    validateStringArray(
      metadataArray(value[property], `${path}.${property}`),
      `${path}.${property}`,
    );
  }
  metadataArray(value.relationAxes, `${path}.relationAxes`);
  optionalMetadataArray(value.fields, `${path}.fields`)?.forEach((field, index) =>
    validateGeneratedField(field, `${path}.fields[${index}]`),
  );
}

function validateGeneratedField(field: unknown, path: string): void {
  const value = metadataObject(field, path);
  expectMetadataString(value.name, `${path}.name`);
  if (!MODEL_FIELD_KINDS.has(value.kind as ModelFieldKind)) {
    throw new Error(
      `${path}.kind must be one of ${[...MODEL_FIELD_KINDS].join(", ")}.`,
    );
  }
}

function validateStringRecord(
  value: Record<string, unknown>,
  path: string,
): void {
  for (const [key, entry] of Object.entries(value)) {
    if (entry == null) continue;
    expectMetadataString(entry, `${path}.${key}`);
  }
}

function validateStringArray(value: readonly unknown[], path: string): void {
  value.forEach((entry, index) =>
    expectMetadataString(entry, `${path}[${index}]`),
  );
}

function expectMetadataString(value: unknown, path: string): void {
  if (typeof value !== "string") {
    throw new Error(`${path} must be a string.`);
  }
}

function metadataArray(value: unknown, path: string): readonly unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${path} must be an array.`);
  }
  return value;
}

function optionalMetadataArray(
  value: unknown,
  path: string,
): readonly unknown[] | undefined {
  if (value == null) return undefined;
  return metadataArray(value, path);
}

function metadataObject(value: unknown, path: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${path} must be an object.`);
  }
  return value as Record<string, unknown>;
}

function optionalMetadataObject(
  value: unknown,
  path: string,
): Record<string, unknown> | undefined {
  if (value == null) return undefined;
  return metadataObject(value, path);
}

function validateDataResourceMetadata(
  schema: GraphQLSchema,
  resources: readonly DataResourceMetadata[],
): void {
  const rootFields = {
    query: schema.getQueryType()?.getFields(),
    mutation: schema.getMutationType()?.getFields(),
    subscription: schema.getSubscriptionType()?.getFields(),
  };
  const seenModelLabels = new Set<string>();
  const seenNodeTypes = new Set<string>();
  for (const resource of resources) {
    if (seenModelLabels.has(resource.modelLabel)) {
      throw new Error(
        `GraphQL schema metadata declares duplicate resource for ` +
          `"${resource.modelLabel}".`,
      );
    }
    seenModelLabels.add(resource.modelLabel);
    const nodeName = resource.typeNames.node;
    const nodeType = nodeName ? schema.getType(nodeName) : null;
    if (!nodeName || !nodeType || !isObjectType(nodeType)) {
      throw new Error(
        `GraphQL schema metadata for "${resource.modelLabel}" references ` +
          `missing object type "${nodeName}".`,
      );
    }
    if (seenNodeTypes.has(nodeName)) {
      throw new Error(
        `GraphQL schema metadata declares duplicate node type "${nodeName}".`,
      );
    }
    seenNodeTypes.add(nodeName);
    for (const [rootKind, rootName] of Object.entries(resource.roots)) {
      if (!rootName) continue;
      const owner = rootOwner(rootKind);
      const fields = rootFields[owner];
      if (!fields || !(rootName in fields)) {
        throw new Error(
          `GraphQL schema metadata for "${resource.modelLabel}" references ` +
            `missing ${ownerRootType(owner)} field "${rootName}" (${rootKind}).`,
        );
      }
    }
    const groupBySpec = resource.typeNames.groupBySpec;
    const groupBySpecType = groupBySpec ? schema.getType(groupBySpec) : null;
    if (
      resource.roots.groups &&
      (!groupBySpec || !groupBySpecType || !isInputObjectType(groupBySpecType))
    ) {
      throw new Error(
        `GraphQL schema metadata for "${resource.modelLabel}" references ` +
          `missing input type "${groupBySpec}".`,
      );
    }
    const groupOrder = resource.typeNames.groupOrder;
    if (resource.roots.groups && groupOrder && !schema.getType(groupOrder)) {
      throw new Error(
        `GraphQL schema metadata for "${resource.modelLabel}" references ` +
          `missing input type "${groupOrder}".`,
      );
    }
    const nodeFields = nodeType.getFields();
    const groupKeyName = resource.typeNames.groupKey;
    const groupKeyType = groupKeyName ? schema.getType(groupKeyName) : null;
    const groupKeyFields = groupKeyType && isObjectType(groupKeyType)
      ? groupKeyType.getFields()
      : {};
    if (resource.roots.groups && (!groupKeyName || !isObjectType(groupKeyType))) {
      throw new Error(
        `GraphQL schema metadata for "${resource.modelLabel}" references ` +
          `missing object type "${groupKeyName}".`,
      );
    }
    for (const dimension of resource.groupDimensions ?? []) {
      if (!resource.groupByFields.includes(dimension.field)) {
        throw new Error(
          `GraphQL schema metadata for "${resource.modelLabel}" declares ` +
            `group dimension "${dimension.field}", but it is not groupable.`,
        );
      }
      validateGroupKeyField(resource, groupKeyFields, dimension.key);
      for (const extraction of dimension.extractions ?? []) {
        validateGroupKeyField(resource, groupKeyFields, extraction.key);
        if (extraction.rangeKey) {
          validateGroupKeyField(resource, groupKeyFields, extraction.rangeKey);
        }
      }
    }
    for (const sort of resource.defaultSort ?? []) {
      if (!resource.orderFields.includes(sort.field)) {
        throw new Error(
          `GraphQL schema metadata for "${resource.modelLabel}" declares ` +
            `default sort field "${sort.field}", but it is not sortable.`,
        );
      }
    }
    for (const alias of resource.groupAliases ?? []) {
      if (!(alias.field in nodeFields)) {
        throw new Error(
          `GraphQL schema metadata for "${resource.modelLabel}" declares ` +
            `group alias field "${alias.field}", but "${resource.typeNames.node}" ` +
            "does not expose that field.",
        );
      }
      if (!resource.groupByFields.includes(alias.aggregateField)) {
        throw new Error(
          `GraphQL schema metadata for "${resource.modelLabel}" declares ` +
            `group alias "${alias.field}" for non-groupable axis ` +
            `"${alias.aggregateField}".`,
        );
      }
    }
  }
}

function validateGroupKeyField(
  resource: DataResourceMetadata,
  groupKeyFields: Record<string, unknown>,
  key: string,
): void {
  if (!(key in groupKeyFields)) {
    throw new Error(
      `GraphQL schema metadata for "${resource.modelLabel}" references ` +
        `missing group key field "${key}".`,
    );
  }
}

function rootOwner(rootKind: string): "query" | "mutation" | "subscription" {
  if (
    rootKind === "create" ||
    rootKind === "update" ||
    rootKind === "delete" ||
    rootKind === "deletePreview"
  ) {
    return "mutation";
  }
  if (rootKind === "changes") return "subscription";
  return "query";
}

function ownerRootType(owner: "query" | "mutation" | "subscription"): string {
  return owner[0]?.toUpperCase() + owner.slice(1);
}

function resourcesByNodeType(
  resources: readonly DataResourceMetadata[],
): Record<string, DataResourceMetadata> {
  return Object.fromEntries(
    resources.flatMap((resource) =>
      resource.typeNames.node ? [[resource.typeNames.node, resource] as const] : [],
    ),
  );
}

function relationAxesByField(
  resource: DataResourceMetadata,
): Record<string, DataResourceRelationAxisMetadata> {
  return Object.fromEntries(
    resource.relationAxes.map((axis) => [axis.field, axis]),
  );
}

function resourceFieldsByName(
  resource: DataResourceMetadata | undefined,
): Record<string, DataResourceFieldMetadata> {
  return Object.fromEntries((resource?.fields ?? []).map((field) => [field.name, field]));
}

function fieldCapabilitiesFromResource(
  field: DataResourceFieldMetadata,
): Pick<
  ModelFieldMetadata,
  | "filterable"
  | "sortable"
  | "aggregatable"
  | "groupable"
  | "readable"
  | "creatable"
  | "updatable"
  | "requiredOnCreate"
> {
  return {
    readable: field.readable,
    filterable: field.filterable,
    sortable: field.sortable,
    aggregatable: field.aggregatable,
    groupable: field.groupable,
    creatable: field.creatable,
    updatable: field.updatable,
    requiredOnCreate: field.requiredOnCreate,
  };
}

function rootFieldsFromResource(
  resource: DataResourceMetadata,
): ModelRootFieldMetadata {
  return withoutUndefined({
    detail: resource.roots.detail ?? undefined,
    list: resource.roots.list ?? undefined,
    aggregate: resource.roots.aggregate ?? undefined,
    groupBy: resource.roots.groups ?? undefined,
    groupByInput: resource.typeNames.groupBySpec ?? undefined,
    groupOrderInput: resource.typeNames.groupOrder ?? undefined,
    revisions: resource.roots.revisions ?? undefined,
    revisionFields: nonEmptyList(resource.revisionFields),
    create: resource.roots.create ?? undefined,
    createFields: nonEmptyList(resource.createFields),
    requiredCreateFields: nonEmptyList(resource.requiredCreateFields),
    update: resource.roots.update ?? undefined,
    updateFields: nonEmptyList(resource.updateFields),
    delete: resource.roots.delete ?? undefined,
    deletePreview: resource.roots.deletePreview ?? undefined,
    changes: resource.roots.changes ?? undefined,
  });
}

function nonEmptyList<T>(value: readonly T[] | undefined): readonly T[] | undefined {
  return value && value.length > 0 ? value : undefined;
}

function withoutUndefined<T extends Record<string, unknown>>(value: T): T {
  return Object.fromEntries(
    Object.entries(value).filter(([, item]) => item !== undefined),
  ) as T;
}

function filterInputForResource(
  schema: GraphQLSchema,
  resource: DataResourceMetadata | undefined,
): GraphQLInputObjectType | null {
  const typeName = resource?.typeNames.filter;
  const type = typeName ? schema.getType(typeName) : null;
  return type && isInputObjectType(type) ? type : null;
}

function groupKeyFieldsForResource(
  schema: GraphQLSchema,
  resource: DataResourceMetadata | undefined,
): ReadonlySet<string> {
  const typeName = resource?.typeNames.groupKey;
  const type = typeName ? schema.getType(typeName) : null;
  return type && isObjectType(type) ? new Set(Object.keys(type.getFields())) : new Set();
}

function relationFilterForField(
  fieldName: string,
  filterInput: GraphQLInputObjectType | null,
  groupKeyFields: ReadonlySet<string>,
  resource?: DataResourceMetadata,
  relationAxis?: DataResourceRelationAxisMetadata,
): ModelRelationFilterMetadata | undefined {
  const filterFields = filterInput?.getFields();
  const inferred = inferredRelationFilterForField(
    fieldName,
    filterFields,
    groupKeyFields,
  );
  if (!resource || !relationAxis) return inferred;
  const filterField = resource.filterFields.includes(relationAxis.field)
    ? relationAxis.field
    : resource.filterFields.includes(`${relationAxis.field}Id`)
      ? `${relationAxis.field}Id`
      : inferred?.field;
  if (!filterField) return inferred;
  const aggregateKey = relationAggregateKey(fieldName, groupKeyFields) ?? inferred?.aggregateKey;
  const labelKey = relationAxis.labelAxis ?? inferred?.labelKey;
  return {
    field: filterField,
    mode: "lookup",
    lookup: relationAxis.publicIdField,
    ...(aggregateKey ? { aggregateKey } : {}),
    ...(labelKey ? { labelKey } : {}),
  };
}

function inferredRelationFilterForField(
  fieldName: string,
  filterFields: ReturnType<GraphQLInputObjectType["getFields"]> | undefined,
  groupKeyFields: ReadonlySet<string>,
): ModelRelationFilterMetadata | undefined {
  if (!filterFields) return undefined;
  const aggregateKey = relationAggregateKey(fieldName, groupKeyFields);
  const labelKey = relationLabelKey(fieldName, groupKeyFields);
  for (const name of [fieldName, `${fieldName}Id`]) {
    const filterField = filterFields[name];
    const shape = filterField ? relationFilterShape(filterField.type) : null;
    if (shape) {
      return {
        field: name,
        ...shape,
        ...(aggregateKey ? { aggregateKey } : {}),
        ...(labelKey ? { labelKey } : {}),
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

/**
 * The group-key field carrying a relation's display label, when the model
 * registers a `<relation>__<label>` group axis. Strawberry camel-cases that path
 * to `<relation>_<Label>` (e.g. `party__display_name` → `party_DisplayName`) —
 * the `<relation>Id` axis has no underscore, so a `<relation>_`-prefixed key is a
 * relation leaf. Returns it only when exactly one exists: with several leaves the
 * intended label is ambiguous, so the group falls back to labelling by id rather
 * than silently picking a schema-order-dependent one.
 */
function relationLabelKey(
  fieldName: string,
  groupKeyFields: ReadonlySet<string>,
): string | undefined {
  const prefix = `${fieldName}_`;
  const leaves = [...groupKeyFields].filter((key) => key.startsWith(prefix));
  return leaves.length === 1 ? leaves[0] : undefined;
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
