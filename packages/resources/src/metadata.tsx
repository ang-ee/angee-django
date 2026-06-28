import {
  createContext,
  createElement,
  useContext,
  useMemo,
  type ReactNode,
} from "react";

/** Field shape classes the backend resource artifact exposes. */
export type ModelFieldKind = "scalar" | "enum" | "relation" | "list";

const MODEL_FIELD_KINDS = new Set<ModelFieldKind>([
  "scalar",
  "enum",
  "relation",
  "list",
]);

export interface ModelEnumValueMetadata {
  value: string;
  description?: string;
}

export type ModelRelationFilterMode = "lookup" | "id";

export interface ModelRelationFilterMetadata {
  field: string;
  mode: ModelRelationFilterMode;
  lookup?: string;
  aggregateKey?: string;
  labelKey?: string;
}

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

export interface ModelRootFieldMetadata {
  detail?: string;
  list?: string;
  aggregate?: string;
  groupBy?: string;
  groupByInput?: string;
  groupOrderInput?: string;
  revisions?: string;
  revisionFields?: readonly string[];
  create?: string;
  createFields?: readonly string[];
  requiredCreateFields?: readonly string[];
  update?: string;
  updateFields?: readonly string[];
  delete?: string;
  deletePreview?: string;
  changes?: string;
}

export interface AngeeSchemaMetadata {
  angee?: {
    resources?: readonly DataResourceMetadata[];
  };
}

export interface DataResourceMetadata {
  schemaName: string;
  modelLabel: string;
  resourceType?: string | null;
  appLabel: string;
  modelName: string;
  publicIdField: string;
  roots: DataResourceRootMetadata;
  typeNames: DataResourceTypeMetadata;
  /**
   * Where list operations (filter/sort/paginate/group) resolve: ``"server"``
   * (Hasura ``where``/``order_by``/``limit`` + the ``_groups`` aggregate) or
   * ``"client"`` (one fetch, then the grid's client row-model pipeline over the
   * loaded set). Defaults to ``"server"`` for an older payload without it.
   */
  rowModel?: "client" | "server";
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

export interface DataResourceOperationTarget {
  dataProviderName: string;
  root: string;
}

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

/**
 * Return whether a resource resolves list operations client-side (one fetch,
 * then the grid's client row-model pipeline). Absent metadata defaults to the
 * server row model.
 */
export function isClientRowModel(
  resource: DataResourceMetadata | null | undefined,
): boolean {
  return resource?.rowModel === "client";
}

export function resourceOperationTarget(
  resource: DataResourceMetadata,
  root: keyof DataResourceRootMetadata,
): DataResourceOperationTarget {
  const value = resource.roots[root];
  if (!value) {
    throw new Error(`Resource "${resource.modelLabel}" does not expose ${root}.`);
  }
  return {
    dataProviderName: resource.schemaName,
    root: value,
  };
}

export interface DataResourceFieldMetadata {
  name: string;
  kind: ModelFieldKind;
  scalar?: string | null;
  values?: readonly ModelEnumValueMetadata[];
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

export interface DataResourceRelationAxisMetadata {
  field: string;
  modelLabel: string;
  publicIdField: string;
  labelAxis?: string | null;
}

export interface DataResourceGroupAliasMetadata {
  field: string;
  aggregateField: string;
  aggregateKey: string;
}

export interface DataResourceGroupBucketFilterValueMapMetadata {
  from: unknown;
  to: unknown;
}

export interface DataResourceGroupBucketFilterMetadata {
  kind: "equality" | "range" | string;
  field: string;
  valueKey?: string | null;
  rangeKey?: string | null;
  lookup?: string | null;
  nullLookup?: string | null;
  valueTransform?: "json" | string | null;
  valueMap?: readonly DataResourceGroupBucketFilterValueMapMetadata[];
}

export interface DataResourceGroupExtractionMetadata {
  name: string;
  input: string;
  key: string;
  rangeKey?: string | null;
  filter?: DataResourceGroupBucketFilterMetadata | null;
}

export interface DataResourceGroupDimensionMetadata {
  field: string;
  input: string;
  key: string;
  kind: "column" | "relation" | string;
  scalar?: string | null;
  filter?: DataResourceGroupBucketFilterMetadata | null;
  extractions?: readonly DataResourceGroupExtractionMetadata[];
}

export interface DataResourceAggregateMeasureMetadata {
  op: string;
  field?: string | null;
  input?: string | null;
}

export interface DataResourceDefaultSortMetadata {
  field: string;
  direction: "ASC" | "DESC" | string;
}

export interface ModelMetadata {
  typeName: string;
  fields: Readonly<Record<string, ModelFieldMetadata>>;
  rootFields?: ModelRootFieldMetadata;
  resource?: DataResourceMetadata;
  recordRepresentation?: string;
}

export interface SchemaFieldMetadata {
  types: Readonly<Record<string, ModelMetadata>>;
  resources?: readonly DataResourceMetadata[];
}

export const EMPTY_SCHEMA_FIELD_METADATA: SchemaFieldMetadata = { types: {} };

const ModelMetadataContext = createContext<SchemaFieldMetadata | null>(null);
const ActiveGraphQLSchemaContext = createContext<string | null>(null);

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

export function schemaFieldMetadataFromAngeeSchemaMetadata(
  metadata: AngeeSchemaMetadata | undefined,
): SchemaFieldMetadata {
  return schemaFieldMetadataFromDataResources(metadata?.angee?.resources ?? []);
}

export function schemaFieldMetadataFromDataResources(
  resources: readonly DataResourceMetadata[],
): SchemaFieldMetadata {
  const types: Record<string, ModelMetadata> = {};
  const seenModelLabels = new Set<string>();
  for (const resource of resources) {
    if (seenModelLabels.has(resource.modelLabel)) {
      throw new Error(
        `GraphQL schema metadata declares duplicate resource for ` +
          `"${resource.modelLabel}".`,
      );
    }
    seenModelLabels.add(resource.modelLabel);
    const typeName =
      resource.typeNames.node ?? `${typeNameForModel(resource.modelLabel)}Type`;
    if (types[typeName]) {
      throw new Error(
        `GraphQL schema metadata declares duplicate node type "${typeName}".`,
      );
    }
    const fields = Object.fromEntries(
      (resource.fields ?? []).map((field) => [
        field.name,
        modelFieldMetadataFromResourceField(field, resource),
      ]),
    );
    const rootFields = rootFieldsFromResource(resource);
    const recordRepresentation = recordRepresentationFor(fields);
    const entry: ModelMetadata = {
      typeName,
      fields,
      rootFields,
      resource,
      ...(recordRepresentation ? { recordRepresentation } : {}),
    };
    types[typeName] = entry;
    // Also index by the model-label-derived type name so `modelMetadataForLabel`
    // resolves a resource whose node type does not follow the `<Model>Type`
    // convention (e.g. a computed `hasura_pydantic_resource` named
    // `PlatformAddonRow` for `platform.Addon`). The label is the lookup key the
    // data view passes (`ListView resource="platform.Addon"`); the node name is a
    // schema detail.
    const labelTypeName = `${typeNameForModel(resource.modelLabel)}Type`;
    if (labelTypeName !== typeName && !types[labelTypeName]) {
      types[labelTypeName] = entry;
    }
  }
  return {
    types,
    ...(resources.length > 0 ? { resources } : {}),
  };
}

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

export function useModelMetadata(modelLabel: string): ModelMetadata | null {
  const metadata = useSchemaFieldMetadata();
  return useMemo(
    () => (modelLabel ? modelMetadataForLabel(metadata, modelLabel) : null),
    [metadata, modelLabel],
  );
}

export function useSchemaFieldMetadata(): SchemaFieldMetadata {
  return useContext(ModelMetadataContext) ?? EMPTY_SCHEMA_FIELD_METADATA;
}

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

export function modelMetadataForLabel(
  metadata: SchemaFieldMetadata,
  modelLabel: string,
): ModelMetadata | null {
  const typeName = typeNameForModel(modelLabel);
  return metadata.types[`${typeName}Type`] ?? metadata.types[typeName] ?? null;
}

export function ActiveGraphQLSchemaProvider({
  schema,
  children,
}: {
  schema: string;
  children: ReactNode;
}): ReactNode {
  return createElement(ActiveGraphQLSchemaContext.Provider, {
    value: schema,
    children,
  });
}

export function useActiveGraphQLSchemaName(): string | null {
  return useContext(ActiveGraphQLSchemaContext);
}

export function typeNameForModel(modelLabel: string): string {
  const segment = modelLabel.split(".").pop() ?? "";
  const name = assertGraphQLName(segment);
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function assertGraphQLName(name: string): string {
  if (!/^[_A-Za-z][_0-9A-Za-z]*$/.test(name)) {
    throw new Error(`Invalid GraphQL name: ${name}`);
  }
  return name;
}

function modelFieldMetadataFromResourceField(
  field: DataResourceFieldMetadata,
  resource: DataResourceMetadata,
): ModelFieldMetadata {
  const relationFilter =
    field.kind === "relation"
      ? relationFilterFromResourceField(field, resource)
      : undefined;
  const relationTarget = relationTargetForField(field, resource);
  return {
    name: field.name,
    kind: field.kind,
    ...(field.scalar ? { scalar: field.scalar } : {}),
    ...(field.kind === "enum" ? { values: field.values ?? [] } : {}),
    ...(relationTarget ? { relationTarget } : {}),
    readable: field.readable,
    filterable: field.filterable,
    sortable: field.sortable,
    aggregatable: field.aggregatable,
    groupable: field.groupable,
    creatable: field.creatable,
    updatable: field.updatable,
    requiredOnCreate: field.requiredOnCreate,
    ...(relationFilter ? { relationFilter } : {}),
  };
}

function relationTargetForField(
  field: DataResourceFieldMetadata,
  resource: DataResourceMetadata,
): string | undefined {
  const modelLabel =
    field.relationModelLabel
    ?? resource.relationAxes.find((axis) => axis.field === field.name)?.modelLabel;
  return modelLabel ? `${typeNameForModel(modelLabel)}Type` : undefined;
}

function relationFilterFromResourceField(
  field: DataResourceFieldMetadata,
  resource: DataResourceMetadata,
): ModelRelationFilterMetadata | undefined {
  const axis = resource.relationAxes.find((candidate) =>
    candidate.field === field.name ||
    candidate.field === snakeFieldName(field.name)
  );
  if (!axis) return undefined;
  const filterField = firstIncluded(resource.filterFields, [
    axis.field,
    field.name,
    `${axis.field}_id`,
    `${field.name}_id`,
    `${axis.field}Id`,
    `${field.name}Id`,
  ]);
  if (!filterField) return undefined;
  const identityDimension = resource.groupDimensions?.find((dimension) =>
    dimension.field === axis.field ||
    dimension.field === field.name ||
    dimension.key === axis.field ||
    dimension.key === field.name
  );
  return {
    field: filterField,
    mode: "lookup",
    lookup: axis.publicIdField,
    ...(identityDimension?.key ? { aggregateKey: identityDimension.key } : {}),
    ...(axis.labelAxis ? { labelKey: axis.labelAxis } : {}),
  };
}

function firstIncluded(
  values: readonly string[],
  candidates: readonly string[],
): string | undefined {
  return candidates.find((candidate) => values.includes(candidate));
}

function snakeFieldName(value: string): string {
  return value.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
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

function recordRepresentationFor(
  fields: Readonly<Record<string, ModelFieldMetadata>>,
): string | undefined {
  const candidates = [
    "title",
    "name",
    "displayName",
    "display_name",
    "fullName",
    "full_name",
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
  optionalMetadataArray(value.groupDimensions, `${path}.groupDimensions`)?.forEach(
    (dimension, index) =>
      validateGeneratedGroupDimension(dimension, `${path}.groupDimensions[${index}]`),
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
  optionalMetadataArray(value.values, `${path}.values`)?.forEach((entry, index) => {
    const enumValue = metadataObject(entry, `${path}.values[${index}]`);
    expectMetadataString(enumValue.value, `${path}.values[${index}].value`);
    if (enumValue.description != null) {
      expectMetadataString(
        enumValue.description,
        `${path}.values[${index}].description`,
      );
    }
  });
}

function validateGeneratedGroupDimension(dimension: unknown, path: string): void {
  const value = metadataObject(dimension, path);
  for (const property of ["field", "input", "key", "kind"]) {
    expectMetadataString(value[property], `${path}.${property}`);
  }
  if (value.scalar != null) {
    expectMetadataString(value.scalar, `${path}.scalar`);
  }
  validateGeneratedGroupBucketFilter(value.filter, `${path}.filter`);
  optionalMetadataArray(value.extractions, `${path}.extractions`)?.forEach(
    (extraction, index) => {
      const extractionValue = metadataObject(
        extraction,
        `${path}.extractions[${index}]`,
      );
      for (const property of ["name", "input", "key"]) {
        expectMetadataString(
          extractionValue[property],
          `${path}.extractions[${index}].${property}`,
        );
      }
      if (extractionValue.rangeKey != null) {
        expectMetadataString(
          extractionValue.rangeKey,
          `${path}.extractions[${index}].rangeKey`,
        );
      }
      validateGeneratedGroupBucketFilter(
        extractionValue.filter,
        `${path}.extractions[${index}].filter`,
      );
    },
  );
}

function validateGeneratedGroupBucketFilter(
  filter: unknown,
  path: string,
): void {
  if (filter == null) return;
  const value = metadataObject(filter, path);
  expectMetadataString(value.kind, `${path}.kind`);
  expectMetadataString(value.field, `${path}.field`);
  for (const property of ["valueKey", "rangeKey", "lookup", "nullLookup", "valueTransform"]) {
    if (value[property] != null) {
      expectMetadataString(value[property], `${path}.${property}`);
    }
  }
  optionalMetadataArray(value.valueMap, `${path}.valueMap`)?.forEach((entry, index) => {
    metadataObject(entry, `${path}.valueMap[${index}]`);
  });
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
