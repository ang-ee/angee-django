import type {
  AngeeSchemaMetadata,
  DataResourceFieldMetadata,
  DataResourceLinesMetadata,
  DataResourceMetadata,
  DataResourceRelationAxisMetadata,
  DataResourceRootMetadata,
} from "./artifact-schema.js";

export { defineAngeeSchemaMetadata } from "./artifact-schema.js";
export type {
  AngeeSchemaMetadata,
  DataResourceAggregateMeasureMetadata,
  DataResourceDefaultSortMetadata,
  DataResourceFieldMetadata,
  DataResourceGroupAliasMetadata,
  DataResourceGroupBucketFilterMetadata,
  DataResourceGroupBucketFilterValueMapMetadata,
  DataResourceGroupDimensionMetadata,
  DataResourceGroupExtractionMetadata,
  DataResourceLinesMetadata,
  DataResourceMetadata,
  DataResourceRelationAxisMetadata,
  DataResourceRootMetadata,
  DataResourceSubtitleMetadata,
  DataResourceTypeMetadata,
  ModelEnumValueMetadata,
  ModelFieldKind,
} from "./artifact-schema.js";

import { canonicalModelLabelOrNull } from "./canonical-model-label.js";

export type ModelRelationFilterMode = "lookup" | "id";

export interface ModelRelationFilterMetadata {
  field: string;
  mode: ModelRelationFilterMode;
  lookup?: string;
  aggregateKey?: string;
  labelKey?: string;
}

/**
 * Presentation-facing field reference. Required wire flags stay owned and
 * validated by `DataResourceFieldMetadata`; callers that only classify a field
 * may supply the required identity pair and any relevant parsed properties.
 */
export type ModelFieldMetadata =
  & Pick<DataResourceFieldMetadata, "name" | "kind">
  & Partial<Pick<
    DataResourceFieldMetadata,
    | "scalar"
    | "values"
    | "widget"
    | "currencyField"
    | "readable"
    | "filterable"
    | "sortable"
    | "aggregatable"
    | "groupable"
    | "creatable"
    | "updatable"
    | "requiredOnCreate"
    | "nullable"
    | "relationModelLabel"
    | "relationLabelAxis"
    | "relationObject"
  >>;

/**
 * A schema-scoped index over one parsed resource. The values in `fields` and
 * `relationAxes` are the original parsed objects; derived presentation and
 * selection facts are resolved on demand by the helpers below.
 */
export interface ModelMetadata {
  resource: DataResourceMetadata;
  fields: Readonly<Record<string, ModelFieldMetadata>>;
  relationAxes: Readonly<Record<string, DataResourceRelationAxisMetadata>>;
}

export interface SchemaFieldMetadata {
  /** Declared GraphQL node-name index. Resources with no node name remain label-addressable. */
  types: Readonly<Record<string, ModelMetadata>>;
  /** Exact canonical model-label index — the collision-free public lookup key. */
  labels: Readonly<Record<string, ModelMetadata>>;
  resources: readonly DataResourceMetadata[];
}

/** A relation-terminal read resolved to scalar GraphQL paths and its display path. */
export interface RelationRepresentationSelection {
  selectionPaths: readonly string[];
  displayPath: string;
}

/** Named build/runtime failure for a relation whose representation cannot resolve. */
export class RelationRepresentationError extends Error {
  override name = "RelationRepresentationError";
}

export interface DataResourceOperationTarget {
  dataProviderName: string;
  root: string;
}

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
  return { dataProviderName: resource.schemaName, root: value };
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
  const labels: Record<string, ModelMetadata> = {};
  for (const resource of resources) {
    if (labels[resource.modelLabel]) {
      throw new Error(
        `GraphQL schema metadata declares duplicate resource for "${resource.modelLabel}".`,
      );
    }
    const fields: Record<string, DataResourceFieldMetadata> = {};
    for (const field of resource.fields ?? []) {
      if (fields[field.name]) {
        throw new Error(
          `Resource "${resource.modelLabel}" declares duplicate field "${field.name}".`,
        );
      }
      fields[field.name] = field;
    }
    const relationAxes: Record<string, DataResourceRelationAxisMetadata> = {};
    for (const axis of resource.relationAxes) {
      if (relationAxes[axis.field]) {
        throw new Error(
          `Resource "${resource.modelLabel}" declares duplicate relation axis "${axis.field}".`,
        );
      }
      relationAxes[axis.field] = axis;
    }
    const model: ModelMetadata = { resource, fields, relationAxes };
    labels[resource.modelLabel] = model;
    const nodeName = resource.typeNames.node;
    if (nodeName) {
      if (types[nodeName]) {
        throw new Error(`GraphQL schema metadata declares duplicate node type "${nodeName}".`);
      }
      types[nodeName] = model;
    }
  }
  return { types, labels, resources };
}

export function modelMetadataForLabel(
  metadata: SchemaFieldMetadata,
  modelLabel: string,
): ModelMetadata | null {
  const canonicalLabel = canonicalModelLabelOrNull(
    metadata.resources,
    modelLabel,
    "model metadata lookup",
  );
  return canonicalLabel ? metadata.labels[canonicalLabel] ?? null : null;
}

/** The canonical relation target label owned by the field or its relation axis. */
export function relationModelLabelForField(
  field: ModelFieldMetadata,
  model?: ModelMetadata | null,
): string | undefined {
  return field.relationModelLabel ?? relationAxisForField(field.name, model)?.modelLabel;
}

export function relationAxisForField(
  fieldName: string,
  model?: ModelMetadata | null,
): DataResourceRelationAxisMetadata | undefined {
  if (!model) return undefined;
  return model.relationAxes[fieldName]
    ?? model.relationAxes[snakeFieldName(fieldName)]
    ?? Object.values(model.relationAxes).find(
      (axis) => snakeFieldName(axis.field) === snakeFieldName(fieldName),
    );
}

/**
 * Resolve a dotted field path when its terminal field is an object relation.
 * Explicit continuation through an unindexed GraphQL object stays structural;
 * metadata is required only when Angee must infer a relation terminal label.
 */
export function relationRepresentationForPath(
  path: string,
  model: ModelMetadata,
  metadata: SchemaFieldMetadata,
): RelationRepresentationSelection | null {
  const segments = path.split(".");
  let current = model;
  for (const [index, segment] of segments.entries()) {
    const field = current.fields[segment];
    if (!field) return null;
    const terminal = index === segments.length - 1;
    if (terminal) {
      if (!hasRelationObjectSelection(field, current)) return null;
      return relationRepresentationSelection(
        path,
        relationModelLabelForField(field, current),
        metadata,
      );
    }
    if (!hasRelationObjectSelection(field, current)) return null;
    const targetLabel = relationModelLabelForField(field, current);
    if (!targetLabel) return null;
    const related = modelMetadataForLabel(metadata, targetLabel);
    if (!related) return null;
    current = related;
  }
  return null;
}

/** Select all readable resource fields, excluding a separately nested field when requested. */
export function resourceReadSelectionPaths(
  model: ModelMetadata,
  metadata: SchemaFieldMetadata,
  excludeField?: string | null,
): readonly string[] {
  const paths = new Set<string>([model.resource.publicIdField]);
  for (const field of Object.values(model.fields)) {
    if (!field.readable || field.name === excludeField || paths.has(field.name)) continue;
    const relation = relationRepresentationForPath(field.name, model, metadata);
    if (relation) {
      for (const path of relation.selectionPaths) paths.add(path);
      continue;
    }
    if (field.kind === "relation" && field.relationObject === false) {
      paths.add(field.name);
      continue;
    }
    if (field.kind === "scalar" || field.kind === "enum" || field.kind === "list") {
      paths.add(field.name);
    }
  }
  return [...paths];
}

/**
 * Selection paths for editable child lines. Uses the line field references
 * directly; no synthetic child model or guessed node type is constructed.
 */
export function lineReadSelectionPaths(
  lines: DataResourceLinesMetadata,
  metadata: SchemaFieldMetadata,
): readonly string[] {
  const paths = new Set<string>(["id"]);
  if (lines.positionField) paths.add(lines.positionField);
  for (const field of lines.fields ?? []) {
    if (field.name === lines.positionField) continue;
    if (field.kind === "relation" && field.relationObject === true) {
      const relation = relationRepresentationSelection(
        field.name,
        field.relationModelLabel ?? undefined,
        metadata,
      );
      for (const path of relation.selectionPaths) paths.add(path);
    } else if (field.kind === "relation" && field.relationObject === false) {
      paths.add(field.name);
    } else if (
      field.kind === "scalar"
      || field.kind === "enum"
      || field.kind === "list"
    ) {
      paths.add(field.name);
    }
  }
  return [...paths];
}

function relationRepresentationSelection(
  prefix: string,
  targetLabel: string | undefined,
  metadata: SchemaFieldMetadata,
): RelationRepresentationSelection {
  if (!targetLabel) {
    throw new RelationRepresentationError(
      `Relation field "${prefix}" does not declare a relation target.`,
    );
  }
  return representationSelection(
    prefix,
    requiredRelationTarget(targetLabel, prefix, metadata),
    metadata,
    new Set(),
  );
}

function representationSelection(
  prefix: string,
  model: ModelMetadata,
  metadata: SchemaFieldMetadata,
  visited: ReadonlySet<string>,
): RelationRepresentationSelection {
  const modelLabel = model.resource.modelLabel;
  if (visited.has(modelLabel)) {
    throw new RelationRepresentationError(
      `Relation representation for "${prefix}" contains a cycle at "${modelLabel}".`,
    );
  }
  const nextVisited = new Set(visited).add(modelLabel);
  const idPath = `${prefix}.${model.resource.publicIdField}`;
  const representation = model.resource.recordRepresentation;
  if (!representation || representation === model.resource.publicIdField) {
    return { selectionPaths: [idPath], displayPath: idPath };
  }
  const nested = relationRepresentationForRepresentation(
    representation,
    model,
    metadata,
    nextVisited,
  );
  if (!nested) {
    const displayPath = `${prefix}.${representation}`;
    return { selectionPaths: [idPath, displayPath], displayPath };
  }
  return {
    selectionPaths: [
      idPath,
      ...nested.selectionPaths.map((path) => `${prefix}.${path}`),
    ],
    displayPath: `${prefix}.${nested.displayPath}`,
  };
}

function relationRepresentationForRepresentation(
  path: string,
  model: ModelMetadata,
  metadata: SchemaFieldMetadata,
  visited: ReadonlySet<string>,
): RelationRepresentationSelection | null {
  const segments = path.split(".");
  let current = model;
  for (const [index, segment] of segments.entries()) {
    const field = current.fields[segment];
    if (!field) {
      throw new RelationRepresentationError(
        `Record representation "${path}" is not declared on "${current.resource.modelLabel}".`,
      );
    }
    const terminal = index === segments.length - 1;
    if (terminal) {
      if (!hasRelationObjectSelection(field, current)) return null;
      const targetLabel = relationModelLabelForField(field, current);
      if (!targetLabel) {
        throw new RelationRepresentationError(
          `Relation field "${path}" does not declare a relation target.`,
        );
      }
      return representationSelection(
        path,
        requiredRelationTarget(targetLabel, path, metadata),
        metadata,
        visited,
      );
    }
    if (!hasRelationObjectSelection(field, current)) return null;
    const targetLabel = relationModelLabelForField(field, current);
    if (!targetLabel) return null;
    const related = modelMetadataForLabel(metadata, targetLabel);
    if (!related) return null;
    current = related;
  }
  return null;
}

function hasRelationObjectSelection(
  field: ModelFieldMetadata,
  model?: ModelMetadata | null,
): boolean {
  if (field.kind !== "relation") return false;
  if (field.relationObject != null) return field.relationObject;
  return relationAxisForField(field.name, model) !== undefined;
}

function requiredRelationTarget(
  modelLabel: string,
  path: string,
  metadata: SchemaFieldMetadata,
): ModelMetadata {
  const target = modelMetadataForLabel(metadata, modelLabel);
  if (!target) {
    throw new RelationRepresentationError(
      `Relation field "${path}" targets missing resource metadata "${modelLabel}".`,
    );
  }
  return target;
}

function relationFilterForAxis(
  axis: DataResourceRelationAxisMetadata,
  resource: DataResourceMetadata,
  fieldName?: string,
): ModelRelationFilterMetadata | undefined {
  const names = fieldName && fieldName !== axis.field ? [axis.field, fieldName] : [axis.field];
  const filterField = firstIncluded(resource.filterFields, [
    ...names,
    ...names.map((name) => `${name}_id`),
    ...names.map((name) => `${name}Id`),
  ]);
  if (!filterField) return undefined;
  const identityDimension = resource.groupDimensions?.find(
    (dimension) => names.includes(dimension.field) || names.includes(dimension.key),
  );
  return {
    field: filterField,
    mode: "lookup",
    lookup: axis.publicIdField,
    ...(identityDimension?.key ? { aggregateKey: identityDimension.key } : {}),
    ...(axis.labelAxis ? { labelKey: axis.labelAxis } : {}),
  };
}

/** Derived relation filter for a projected field or an axis-only relation. */
export function relationFilterForRelation(
  relationField: string,
  metadata: ModelMetadata | null,
): ModelRelationFilterMetadata | undefined {
  if (!metadata) return undefined;
  const axis = relationAxisForField(relationField, metadata);
  return axis
    ? relationFilterForAxis(axis, metadata.resource, relationField)
    : undefined;
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
