/**
 * Browser-free metadata ingestion, indexing, and selection surface. This
 * subpath is the shared owner for the React runtime and the plain Node CLI.
 */
export {
  defineAngeeSchemaMetadata,
  lineReadSelectionPaths,
  modelMetadataForLabel,
  relationModelLabelForField,
  relationRepresentationForPath,
  resourceReadSelectionPaths,
  schemaFieldMetadataFromAngeeSchemaMetadata,
  schemaFieldMetadataFromDataResources,
  RelationRepresentationError,
  type AngeeSchemaMetadata,
  type DataResourceFieldMetadata,
  type DataResourceLinesMetadata,
  type DataResourceMetadata,
  type ModelFieldMetadata,
  type ModelMetadata,
  type RelationRepresentationSelection,
  type SchemaFieldMetadata,
} from "./artifact.js";

export { ResourceQuery, GroupAxis, QueryParseError, type QueryFilter, type FilterRecord, type FilterValue, type FilterPrimitive, type GroupBucket, type GroupProjection, type LocalQueryField } from "./query.js";
export { FILTER_OPERATORS, GroupSpecSchema, GroupSpecsSchema, DataResourceQuerySchema, type FilterOperator, type DataResourceQuery, type QueryField, type QueryAxis, type QueryDrill, type QueryExtraction, type GroupSpec, type QuerySort } from "./query-schema.js";

export { Filter, isQueryFilter, type FilterFacet } from "./filter.js";
