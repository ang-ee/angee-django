import type {
  AngeeSchemaMetadata,
  DataResourceMetadata,
  DataResourceLinesMetadata,
  DataResourceRootMetadata,
  DataResourceFieldMetadata,
  DataResourceRelationAxisMetadata,
  ModelEnumValueMetadata,
  ModelFieldKind,
} from "./artifact-schema";
export { defineAngeeSchemaMetadata } from "./artifact-schema";
export type {
  AngeeSchemaMetadata,
  DataResourceMetadata,
  DataResourceSubtitleMetadata,
  DataResourceLinesMetadata,
  DataResourceRootMetadata,
  DataResourceTypeMetadata,
  DataResourceFieldMetadata,
  DataResourceRelationAxisMetadata,
  DataResourceGroupAliasMetadata,
  DataResourceGroupBucketFilterValueMapMetadata,
  DataResourceGroupBucketFilterMetadata,
  DataResourceGroupExtractionMetadata,
  DataResourceGroupDimensionMetadata,
  DataResourceAggregateMeasureMetadata,
  DataResourceDefaultSortMetadata,
  ModelEnumValueMetadata,
  ModelFieldKind,
} from "./artifact-schema";
import { canonicalModelLabelOrNull } from "./canonical-model-label";
import { modelLabelSegment } from "./naming";

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
  /** Backend-owned widget key (e.g. `"money"`); wins over the scalar-derived default. */
  widget?: string;
  /** For a money field: the path to the FK that owns the row's currency. */
  currencyField?: string;
  relationTarget?: string;
  /** A `relation` field projected as a nested selectable object (vs a public-id
   * scalar). A generated relation axis/filter is the equivalent proof for nodes
   * whose raw projection flag remains false. */
  relationObject?: boolean;
  relationFilter?: ModelRelationFilterMetadata;
  filterable?: boolean;
  sortable?: boolean;
  aggregatable?: boolean;
  groupable?: boolean;
  readable?: boolean;
  nullable?: boolean;
  creatable?: boolean;
  updatable?: boolean;
  requiredOnCreate?: boolean;
}

/**
 * A relation-terminal read resolved to the concrete scalar paths GraphQL must
 * select and the one path a list cell displays.
 */
export interface RelationRepresentationSelection {
  selectionPaths: readonly string[];
  displayPath: string;
}

/** Named build/runtime failure for a relation whose representation cannot resolve. */
export class RelationRepresentationError extends Error {
  override name = "RelationRepresentationError";
}

export interface ModelRootFieldMetadata {
  detail?: string;
  list?: string;
  aggregate?: string;
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

export interface DataResourceOperationTarget {
  dataProviderName: string;
  root: string;
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

export interface ModelMetadata {
  typeName: string;
  fields: Readonly<Record<string, ModelFieldMetadata>>;
  rootFields?: ModelRootFieldMetadata;
  resource?: DataResourceMetadata;
  recordRepresentation?: string;
}

export interface SchemaFieldMetadata {
  types: Readonly<Record<string, ModelMetadata>>;
  /** Exact model-label index — the collision-free lookup key for data views. */
  labels?: Readonly<Record<string, ModelMetadata>>;
  resources?: readonly DataResourceMetadata[];
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
  const seenModelLabels = new Set<string>();
  const legacyTypeFallbacks: [string, ModelMetadata][] = [];
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
    const recordRepresentation = resource.recordRepresentation ?? undefined;
    const entry: ModelMetadata = {
      typeName,
      fields,
      rootFields,
      resource,
      ...(recordRepresentation ? { recordRepresentation } : {}),
    };
    types[typeName] = entry;
    labels[resource.modelLabel] = entry;
    const labelTypeName = `${typeNameForModel(resource.modelLabel)}Type`;
    if (labelTypeName !== typeName) {
      legacyTypeFallbacks.push([labelTypeName, entry]);
    }
  }
  // Also index by the model-label-derived type name so `modelMetadataForLabel`
  // resolves a resource whose node type does not follow the `<Model>Type`
  // convention (e.g. a computed `hasura_pydantic_resource` named
  // `PlatformAddonRow` for `platform.Addon`). Declared node names are contracts:
  // they register first, win over every fallback, and duplicate declarations
  // fail above. These fallbacks are only a best-effort legacy affordance, so a
  // fallback collision drops the ambiguous name instead of throwing; both
  // resources remain available through their collision-free label index.
  const declaredTypeNames = new Set(Object.keys(types));
  const claimedFallbackNames = new Set<string>();
  const ambiguousFallbackNames = new Set<string>();
  for (const [typeName, entry] of legacyTypeFallbacks) {
    if (
      declaredTypeNames.has(typeName) ||
      ambiguousFallbackNames.has(typeName)
    ) {
      continue;
    }
    if (claimedFallbackNames.has(typeName)) {
      delete types[typeName];
      ambiguousFallbackNames.add(typeName);
      continue;
    }
    claimedFallbackNames.add(typeName);
    types[typeName] = entry;
  }
  return {
    types,
    labels,
    ...(resources.length > 0 ? { resources } : {}),
  };
}

export function typeNameForModel(modelLabel: string): string {
  const segment = modelLabelSegment(modelLabel);
  const name = assertGraphQLName(segment);
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export function modelMetadataForLabel(
  metadata: SchemaFieldMetadata,
  modelLabel: string,
): ModelMetadata | null {
  // The label is the unique key (duplicate labels are a build error); declared
  // node names can differ from the model-label-derived fallback
  // (`iam.Relationship` declares `RebacRelationshipType`), and those fallbacks
  // exist only for hand-built metadata without a label index.
  const canonicalLabel = canonicalModelLabelOrNull(
    metadata.resources ?? [],
    modelLabel,
    "model metadata lookup",
  );
  if (!canonicalLabel) return null;
  const exact = metadata.labels?.[canonicalLabel];
  if (exact) return exact;
  const resource = metadata.resources?.find(
    (candidate) => candidate.modelLabel === canonicalLabel,
  );
  const declaredType = resource?.typeNames.node;
  if (declaredType && metadata.types[declaredType]) {
    return metadata.types[declaredType] ?? null;
  }
  const typeName = typeNameForModel(canonicalLabel);
  return metadata.types[`${typeName}Type`] ?? metadata.types[typeName] ?? null;
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
  // `relationAxes` is the owner of which fields are to-one relations, and
  // `relationFilterFromResourceField` already returns undefined for anything it
  // does not name. Gating on `kind` here instead would drop every relation the
  // node projects as a bare `ID` scalar (see `isScalarIdRelation`), leaving it
  // with no filter, no identity axis, and buckets labelled by raw public id.
  const relationFilter = relationFilterFromResourceField(field, resource);
  const relationTarget = relationTargetForField(field, resource);
  return {
    ...baseModelFieldMetadata(field, relationTarget),
    ...(relationFilter ? { relationFilter } : {}),
  };
}

/**
 * The resource-independent half of a field's model metadata: name, kind, scalar,
 * widget, currency path, enum values, and relation target. Shared by the
 * top-level resource projection (which then folds in the relation filter from the
 * resource's axes) and the F6 child-lines projection (whose fields carry their
 * relation target directly and need no parent-resource axes).
 */
function baseModelFieldMetadata(
  field: DataResourceFieldMetadata,
  relationTarget: string | undefined,
): ModelFieldMetadata {
  return {
    name: field.name,
    kind: field.kind,
    ...(field.scalar ? { scalar: field.scalar } : {}),
    ...(field.widget ? { widget: field.widget } : {}),
    ...(field.currencyField ? { currencyField: field.currencyField } : {}),
    ...(field.kind === "enum" ? { values: field.values ?? [] } : {}),
    ...(relationTarget ? { relationTarget } : {}),
    ...(field.relationObject ? { relationObject: true } : {}),
    readable: field.readable,
    nullable: field.nullable ?? false,
    filterable: field.filterable,
    sortable: field.sortable,
    aggregatable: field.aggregatable,
    groupable: field.groupable,
    creatable: field.creatable,
    updatable: field.updatable,
    requiredOnCreate: field.requiredOnCreate,
  };
}

/**
 * Project one editable-lines contract into a `ModelMetadata` for the child model,
 * so `EditableLines` resolves each line cell's widget, options, currency path, and
 * relation target through the same field classifier the parent form uses. The
 * child columns carry their own `relationModelLabel`, so no parent-resource axes
 * are needed; relation cells resolve their target through the full schema metadata
 * the caller already holds.
 */
export function lineChildModelMetadata(
  lines: DataResourceLinesMetadata,
): ModelMetadata {
  const fields = Object.fromEntries(
    (lines.fields ?? []).map((field) => [
      field.name,
      baseModelFieldMetadata(field, relationTargetForLineField(field)),
    ]),
  );
  return {
    typeName: `${typeNameForModel(lines.modelLabel)}Type`,
    fields,
  };
}

/**
 * The GraphQL selection paths a detail (`*_by_pk`) read must include for a
 * resource's editable child lines (F6), relative to the parent's lines field.
 * Mirrors the parent form's own field selection: the child `id`, the order
 * column, each editable scalar/enum column by name, and each relation column as
 * its nested `id` plus the related type's record representation — so a loaded
 * line cell labels its relation with no extra round-trip, exactly like the
 * `<resource>_save` return that already selects these children. Without this the
 * detail read drops the lines and the form shows none even when the record has
 * them. The caller prefixes each path with `<linesResource.field>.`.
 */
export function lineReadSelectionPaths(
  lines: DataResourceLinesMetadata,
  metadata: SchemaFieldMetadata,
): readonly string[] {
  const child = lineChildModelMetadata(lines);
  const paths = new Set<string>(["id"]);
  if (lines.positionField) paths.add(lines.positionField);
  for (const field of Object.values(child.fields)) {
    if (field.kind === "relation") {
      const representation = relationRepresentationSelection(
        field.name,
        field.relationTarget,
        metadata,
      );
      for (const path of representation.selectionPaths) paths.add(path);
    } else {
      paths.add(field.name);
    }
  }
  return [...paths];
}

/**
 * Resolve a dotted field path when its terminal field is an object relation.
 *
 * The returned selection always includes the related record id plus every
 * scalar path needed by its `recordRepresentation`; `displayPath` is the final
 * representation scalar the column renderer reads. A non-relation terminal (or
 * an id-projected relation scalar) returns `null`. An explicit continuation may
 * also return `null` when its intermediate target has no metadata resource; the
 * caller can still select and read that author-declared dotted path structurally.
 * Missing relation/type facts at the relation terminal throw a named error
 * instead of allowing a caller to emit a bare GraphQL object leaf.
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
      if (!hasRelationObjectSelection(field)) return null;
      return relationRepresentationSelection(path, field.relationTarget, metadata);
    }
    if (
      !hasRelationObjectSelection(field)
      || !field.relationTarget
    ) {
      return null;
    }
    const related = metadata.types[field.relationTarget];
    // A continued path already declares the leaf the author wants. Some valid
    // GraphQL node targets deliberately have no resource metadata, so leave the
    // dotted path intact and let the selection builder nest it structurally.
    // Metadata remains mandatory below when the path ends at a relation and its
    // record representation must be derived.
    if (!related) return null;
    current = related;
  }
  return null;
}

function relationRepresentationSelection(
  prefix: string,
  relationTarget: string | undefined,
  metadata: SchemaFieldMetadata,
): RelationRepresentationSelection {
  if (!relationTarget) {
    throw new RelationRepresentationError(
      `Relation field "${prefix}" does not declare a relation target.`,
    );
  }
  const related = requiredRelationTarget(relationTarget, prefix, metadata);
  return representationSelection(prefix, related, metadata, new Set());
}

function representationSelection(
  prefix: string,
  model: ModelMetadata,
  metadata: SchemaFieldMetadata,
  visited: ReadonlySet<string>,
): RelationRepresentationSelection {
  if (visited.has(model.typeName)) {
    throw new RelationRepresentationError(
      `Relation representation for "${prefix}" contains a cycle at "${model.typeName}".`,
    );
  }
  const nextVisited = new Set(visited).add(model.typeName);
  const idPath = `${prefix}.id`;
  const representation = model.recordRepresentation;
  if (!representation || representation === "id") {
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
    return {
      selectionPaths: [idPath, displayPath],
      displayPath,
    };
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
        `Record representation "${path}" is not declared on "${current.typeName}".`,
      );
    }
    const terminal = index === segments.length - 1;
    if (terminal) {
      if (!hasRelationObjectSelection(field)) return null;
      if (!field.relationTarget) {
        throw new RelationRepresentationError(
          `Relation field "${path}" does not declare a relation target.`,
        );
      }
      const related = requiredRelationTarget(field.relationTarget, path, metadata);
      return representationSelection(path, related, metadata, visited);
    }
    if (
      !hasRelationObjectSelection(field)
      || !field.relationTarget
    ) {
      return null;
    }
    current = requiredRelationTarget(field.relationTarget, path, metadata);
  }
  return null;
}

function hasRelationObjectSelection(field: ModelFieldMetadata): boolean {
  // `relationObject` is the direct node-projection signal. A declared relation
  // axis/filter is the second generated proof used by current Hasura nodes: the
  // field can still be an object in the SDL even when the raw projection flag is
  // false (projects.Project.product is the live example). A relation carrying
  // neither proof may be an ID scalar and must remain a leaf.
  return field.kind === "relation"
    && (field.relationObject === true || field.relationFilter !== undefined);
}

function requiredRelationTarget(
  typeName: string,
  path: string,
  metadata: SchemaFieldMetadata,
): ModelMetadata {
  const target = metadata.types[typeName];
  if (!target) {
    throw new RelationRepresentationError(
      `Relation field "${path}" targets missing metadata type "${typeName}".`,
    );
  }
  return target;
}

function relationTargetForLineField(
  field: DataResourceFieldMetadata,
): string | undefined {
  return field.relationModelLabel
    ? `${typeNameForModel(field.relationModelLabel)}Type`
    : undefined;
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
  return axis ? relationFilterForAxis(axis, resource, field.name) : undefined;
}

/**
 * The filter/identity/label contract of one relation axis.
 *
 * The axis owns the relation: its public-id lookup, the identity dimension a
 * bucket drills down on, and the label axis a bucket renders. `fieldName` only
 * widens the name candidates for a node that projects the relation under its own
 * spelling — the axis alone is enough, because a node need not expose the FK at
 * all (a curated node may project `provider_slug` and hide `oauth_client`).
 */
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
  const identityDimension = resource.groupDimensions?.find((dimension) =>
    names.includes(dimension.field) || names.includes(dimension.key)
  );
  return {
    field: filterField,
    mode: "lookup",
    lookup: axis.publicIdField,
    ...(identityDimension?.key ? { aggregateKey: identityDimension.key } : {}),
    ...(axis.labelAxis ? { labelKey: axis.labelAxis } : {}),
  };
}

/**
 * The relation contract for one relation field, asked of the resource rather than
 * of the node's field list — so a relation the node does not project still groups
 * by its identity and renders its label instead of a raw public id.
 */
export function relationFilterForRelation(
  relationField: string,
  metadata: ModelMetadata | null,
): ModelRelationFilterMetadata | undefined {
  const onField = metadata?.fields[relationField]?.relationFilter;
  if (onField) return onField;
  const resource = metadata?.resource;
  const axis = resource?.relationAxes.find((candidate) => candidate.field === relationField);
  return axis && resource ? relationFilterForAxis(axis, resource) : undefined;
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
