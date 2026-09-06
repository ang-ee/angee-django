import type {
  DataResourceRelationAxisMetadata,
  DataResourceMetadata,
  ModelMetadata,
  SchemaFieldMetadata,
} from "./artifact";
import { modelLabelSegment } from "./naming";

/** Minimal generated-resource fixture shared by framework package tests. */
export function testDataResource(
  modelLabel: string,
  overrides: Partial<DataResourceMetadata> = {},
): DataResourceMetadata {
  const segment = modelLabelSegment(modelLabel);
  const modelName = overrides.modelName ?? segment.toLowerCase();
  const separator = modelLabel.lastIndexOf(".");
  const { roots, typeNames, ...rest } = overrides;
  const list = `${modelName}s`;
  return {
    schemaName: "console",
    modelLabel,
    appLabel: separator < 0 ? "" : modelLabel.slice(0, separator),
    modelName,
    publicIdField: "id",
    roots: {
      list,
      detail: `${list}_by_pk`,
      create: `insert_${list}_one`,
      update: `update_${list}_by_pk`,
      delete: `delete_${list}_by_pk`,
      ...roots,
    },
    typeNames: { node: `${segment}Type`, ...typeNames },
    capabilities: ["list", "detail", "create", "update", "delete"],
    fields: [],
    filterFields: [],
    orderFields: [],
    aggregateFields: [],
    groupByFields: [],
    relationAxes: [],
    ...rest,
  };
}

/**
 * Fill the canonical resource/label indexes for a hand-authored test metadata
 * object whose model entries already carry their resource facts.
 */
export function withTestResourceInventory(
  metadata: {
    types: Readonly<Record<
      string,
      Omit<ModelMetadata, "relationAxes"> & {
        relationAxes?: Readonly<Record<string, DataResourceRelationAxisMetadata>>;
      }
    >>;
  },
): SchemaFieldMetadata {
  const types: Record<string, ModelMetadata> = {};
  const labels: Record<string, ModelMetadata> = {};
  const resources: DataResourceMetadata[] = [];
  for (const model of Object.values(metadata.types)) {
    const relationAxes = model.relationAxes ?? Object.fromEntries(
      model.resource.relationAxes.map((axis) => [axis.field, axis]),
    );
    const indexed: ModelMetadata = { ...model, relationAxes };
    resources.push(indexed.resource);
    labels[indexed.resource.modelLabel] = indexed;
    const nodeName = indexed.resource.typeNames.node;
    if (nodeName) types[nodeName] = indexed;
  }
  return { types, labels, resources };
}
