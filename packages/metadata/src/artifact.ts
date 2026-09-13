import type {
  AngeeSchemaMetadata,
  DataResourceFieldMetadata,
  DataResourceLinesMetadata,
  DataResourceMetadata,
  DataResourceRootMetadata,
} from "./artifact-schema.js";

export { defineAngeeSchemaMetadata } from "./artifact-schema.js";
export type {
  AngeeSchemaMetadata,
  DataResourceAggregateMeasureMetadata,
  DataResourceSubjectSpecies,
  DataResourceGrantableRelation,
  DataResourceFieldMetadata,
  DataResourceLinesMetadata,
  DataResourceMetadata,
  DataResourceRootMetadata,
  DataResourceSubtitleMetadata,
  DataResourceTypeMetadata,
  ModelEnumValueMetadata,
  ModelFieldKind,
} from "./artifact-schema.js";

import { canonicalModelLabelOrNull } from "./canonical-model-label.js";

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
    | "aggregatable"
    | "creatable"
    | "updatable"
    | "requiredOnCreate"
    | "nullable"
    | "relationModelLabel"
    | "relationObject"
  >>;

/**
 * A schema-scoped index over one parsed resource. The values in `fields` and
 * query are the original parsed objects; derived presentation and
 * selection facts are resolved on demand by the helpers below.
 */
export interface ModelMetadata {
  resource: DataResourceMetadata;
  fields: Readonly<Record<string, ModelFieldMetadata>>;
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
  modelLabel: string;
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
  return { dataProviderName: resource.schemaName, root: value, modelLabel: resource.modelLabel };
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
    const model: ModelMetadata = { resource, fields };
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

/** The final projected field owns its relation target. */
export function relationModelLabelForField(
  field: ModelFieldMetadata,
  model?: ModelMetadata | null,
): string | undefined {
  return field.relationModelLabel ?? model?.resource.query.fields[field.name]?.relation?.model;
}

/**
 * Resolve a dotted field path when its terminal field is an object relation.
 * Explicit continuation through an unindexed GraphQL object stays structural;
 * final query relation paths own all inferred object selections.
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
      const relation = current.resource.query.fields[segment]?.relation;
      const displayPath = relation?.labelPath ?? relation?.identityPath;
      if (!relation || !displayPath) {
        throw new RelationRepresentationError(`Relation field "${path}" has no finalized selectable representation.`);
      }
      const prefix = segments.slice(0, index).join(".");
      const qualify = (value: string) => prefix ? `${prefix}.${value}` : value;
      return {
        selectionPaths: [...new Set([
          ...(relation.identityPath ? [qualify(relation.identityPath)] : []),
          qualify(displayPath),
        ])],
        displayPath: qualify(displayPath),
      };
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
  const paths = new Set<string>([model.resource.query.identity.field]);
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
    // The finalized query owns selectable leaves. Object-valued lists need
    // an authored nested selection and have no scalar row paths.
    for (const path of model.resource.query.fields[field.name]?.row?.paths ?? []) {
      paths.add(path);
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
    if (
      (field.kind === "relation" && field.relationObject === true)
      || (field.kind === "list" && field.scalar == null && field.relationModelLabel)
    ) {
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
      || (field.kind === "list" && field.scalar != null)
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
  const idPath = `${prefix}.${model.resource.query.identity.field}`;
  const representation = model.resource.recordRepresentation;
  if (!representation || representation === model.resource.query.identity.field) {
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
  const relation = model?.resource.query.fields[field.name]?.relation;
  return relation != null && relation.identityPath !== field.name;
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
