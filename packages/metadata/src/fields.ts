import {
  relationModelLabelForField,
  type DataResourceMetadata,
  type ModelFieldMetadata,
  type ModelMetadata,
} from "./artifact.js";
import type { CrudFilter } from "@refinedev/core";
import { Filter } from "./filter.js";
import { ResourceQuery, type QueryFilter } from "./query.js";
import type { QueryField } from "./query-schema.js";
import type { Row } from "./rows.js";

/** A to-one relation explicitly projected by the node as a scalar identity. */
export function isScalarIdRelation(
  field: ModelFieldMetadata,
  model?: ModelMetadata | null,
): boolean {
  return field.kind === "relation" && field.relationObject === false
    && relationModelLabelForField(field, model) !== undefined;
}

/** Final field metadata owns relation classification for both projection shapes. */
export function isToOneRelationField(
  field: ModelFieldMetadata | undefined,
  _model?: ModelMetadata | null,
): boolean {
  return field?.kind === "relation";
}

const SCALAR_WIDGET: Readonly<Record<string, string>> = {
  Boolean: "switch",
  Int: "integer",
  Float: "float",
  Decimal: "float",
  DateTime: "datetime",
  Date: "date",
  Time: "time",
  JSON: "json",
};

export type ResourceFilterFieldType =
  | "boolean"
  | "date"
  | "datetime"
  | "number"
  | "selection"
  | "text"
  | "time";

export interface ChoiceFacetSupport {
  fieldName: string;
  field?: Pick<QueryField, "kind" | "scalar">;
  hasOptions?: boolean;
  hasTone?: boolean;
  allowStatusFallback?: boolean;
}

/**
 * The default widget family for a generated resource field. The backend owns the
 * widget vocabulary (`angee.graphql.data.resource_fields`). Status-display
 * metadata selects the status owner; otherwise an explicit `widget` — e.g.
 * `"money"` over a Decimal scalar — wins. A field with no backend widget falls
 * back to the kind/scalar-derived default: a computed, model-less field, and a
 * to-many relation, which the backend leaves unclassified. A list carrying a
 * relation target picks its related records (`many2many`); a plain value list is
 * a tag input. UI owns the actual component registry.
 */
export function defaultWidgetForModelField(
  field: ModelFieldMetadata | undefined,
  model?: ModelMetadata | null,
): string | undefined {
  if (!field) return undefined;
  if (field.statusDisplay) return "statusBadge";
  if (field.widget) return field.widget;
  if (field.kind === "enum") return "select";
  if (field.kind === "relation") return "many2one";
  if (field.kind === "list") {
    return relationModelLabelForField(field, model) ? "many2many" : "tagInput";
  }
  return field.scalar ? SCALAR_WIDGET[field.scalar] : undefined;
}

export function filterFieldType(
  fieldName: string,
  field: Pick<QueryField, "kind" | "scalar"> | undefined,
  support: Omit<ChoiceFacetSupport, "fieldName" | "field"> = {},
): ResourceFilterFieldType | null {
  if (field?.kind === "enum") return "selection";
  if (field?.kind === "scalar" && field.scalar === "String") return "text";
  if (field?.kind === "scalar" && field.scalar === "Boolean") return "boolean";
  if (
    field?.kind === "scalar" &&
    (field.scalar === "Int" ||
      field.scalar === "Float" ||
      field.scalar === "Decimal")
  ) {
    return "number";
  }
  if (field?.kind === "scalar" && field.scalar === "Time") return "time";
  if (isDateField(field, fieldName)) {
    return field?.scalar === "Date" ? "date" : "datetime";
  }
  return supportsChoiceFacet({ fieldName, field, ...support }) ? "selection" : null;
}

/** Whether the resource's update root accepts writes for a field. */
export function fieldUpdatable(
  metadata: ModelMetadata | null | undefined,
  fieldName: string,
): boolean {
  return resourceFieldUpdatable(metadata?.resource, fieldName, metadata?.fields[fieldName]);
}

/**
 * Whether a person may change a field on a stored record through the resource's update: it is
 * updatable and not create-only. A create-only selector rides the update input only for its repair,
 * which the record form offers on its own terms, so a list, a board or any other edit surface asks this.
 */
export function fieldChangeable(
  metadata: ModelMetadata | null | undefined,
  fieldName: string,
): boolean {
  return fieldUpdatable(metadata, fieldName) && metadata?.fields[fieldName]?.createOnly !== true;
}

function resourceFieldUpdatable(
  resource: DataResourceMetadata | null | undefined,
  fieldName: string,
  field: ModelFieldMetadata | undefined,
): boolean {
  return Boolean(resource?.roots.update)
    && (!resource?.updateFields || resource.updateFields.includes(fieldName))
    && field?.updatable !== false;
}

/**
 * The resource's trash flag field, when its metadata classifies one trashable.
 * The backend owns the one column name; shared views read this facet to offer
 * the trash and restore verbs and a removed-records list.
 */
export function trashFlagField(
  resource: DataResourceMetadata | null | undefined,
): string | null {
  return resource?.fields?.find((field) => field.trashable === true)?.name ?? null;
}

/** The resource's archive flag, classified by the backend rather than its name. */
export function archiveFlagField(
  resource: DataResourceMetadata | null | undefined,
): string | null {
  return resource?.fields?.find((field) => field.archivable === true)?.name ?? null;
}

export interface ResourceLifecycle {
  /** Flag accepted by the update root, or null when Archive is unavailable. */
  archiveFlag: string | null;
  /** Filterability is independent of whether this resource permits writes. */
  archiveFilter: { flag: string; field: string } | null;
  /** Flag with a resource type for the shared trash and restore verbs. */
  trashFlag: string | null;
  canDelete: boolean;
  /** Preserve flag projections even on read-only resources. */
  flagFields: readonly string[];
}

/** Generic lifecycle capabilities from the resource's emitted contracts. */
export function resourceLifecycle(
  resource: DataResourceMetadata | null | undefined,
  model?: ModelMetadata | null,
): ResourceLifecycle {
  const archive = archiveFlagField(resource);
  const trash = trashFlagField(resource);
  const filter = resource && archive ? ResourceQuery.from(resource).fields[archive]?.filter : undefined;
  const archiveField = archive
    ? model ? model.fields[archive] : resource?.fields?.find((field) => field.name === archive)
    : undefined;
  return {
    archiveFlag: archive && resourceFieldUpdatable(resource, archive, archiveField) ? archive : null,
    archiveFilter: archive && filter?.operators.includes("exact") ? { flag: archive, field: filter.field } : null,
    trashFlag: trash && resource?.resourceType ? trash : null,
    canDelete: Boolean(resource?.roots.deletePreview),
    flagFields: [archive, trash].filter((flag): flag is string => flag !== null),
  };
}

/** A trashable row must already be in the trash. Generic Delete needs a preview
 * root; a form with an authored Delete action supplies that action's admission.
 */
export function canDeleteRecord(
  resource: DataResourceMetadata | null | undefined,
  record: Row | null | undefined,
  offered = resourceLifecycle(resource).canDelete,
): boolean {
  const trash = trashFlagField(resource);
  return offered && (!trash || record?.[trash] === true);
}

/**
 * Narrow a collection to unarchived rows only when metadata declares the flag,
 * its query contract supports exact filtering, and no supplied predicate names it.
 * Picker transports may also supply native Refine filters, whose field names are
 * wire paths; these stay native while this owner scopes the canonical filter.
 */
export function unarchivedFilter(
  resource: DataResourceMetadata | null | undefined,
  filter: QueryFilter,
  nativeFilters?: readonly CrudFilter[],
): QueryFilter {
  const archive = resourceLifecycle(resource).archiveFilter;
  if (!archive) return filter;
  const { flag, field } = archive;
  if (Filter.references(filter, flag)
    || Filter.references(nativeFilters, field)) return filter;
  return Filter.combine(filter, { [flag]: { exact: false } });
}

export function supportsChoiceFacet(support: ChoiceFacetSupport): boolean {
  if (support.field?.kind === "enum") return true;
  if (support.hasOptions) return true;
  if (support.hasTone) return true;
  return support.allowStatusFallback === true && support.fieldName === "status";
}

function looksLikeDateField(fieldName: string): boolean {
  const normalized = fieldName.toLowerCase();
  return normalized.endsWith("at") ||
    normalized.endsWith("_at") ||
    normalized.endsWith("date") ||
    normalized.endsWith("_date") ||
    normalized.endsWith("on") ||
    normalized.endsWith("_on");
}

/** Resolve date semantics from declared metadata, with a name fallback only when absent. */
export function isDateField(
  field: Pick<QueryField, "kind" | "scalar"> | undefined,
  fieldName: string,
): boolean {
  if (field) {
    return field.kind === "scalar" &&
      (field.scalar === "DateTime" || field.scalar === "Date");
  }
  return looksLikeDateField(fieldName);
}

/** Resolve an authored display path through its declared query sort capability. */
export function resourceOrderFieldForPath(
  path: string,
  resource: DataResourceMetadata | null | undefined,
): string | null {
  return resource ? resource.query.fields[path]?.sort?.field ?? null : path;
}
