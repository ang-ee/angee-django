/**
 * Browser-free metadata ingestion, indexing, and selection surface. This
 * subpath is the shared owner for the React runtime and the plain Node CLI.
 */
export {
  defineAngeeSchemaMetadata,
  lineReadSelectionPaths,
  modelMetadataForLabel,
  relationAxisForField,
  relationFilterForRelation,
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
  type DataResourceRelationAxisMetadata,
  type ModelFieldMetadata,
  type ModelMetadata,
  type RelationRepresentationSelection,
  type SchemaFieldMetadata,
} from "./artifact.js";
