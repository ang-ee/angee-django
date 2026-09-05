import * as v from "valibot";

/** The generated resource wire contract; output types come from these schemas. */
const FieldKindSchema = v.picklist(["scalar", "enum", "relation", "list"]);
const OptionalString = v.nullish(v.string());
const Strings = v.pipe(v.array(v.string()), v.readonly());
const EnumValueSchema = v.looseObject({
  value: v.string(),
  description: OptionalString,
});
const ResourceFieldSchema = v.looseObject({
  name: v.string(),
  kind: FieldKindSchema,
  scalar: OptionalString,
  values: v.optional(v.pipe(v.array(EnumValueSchema), v.readonly())),
  widget: OptionalString,
  currencyField: OptionalString,
  readable: v.boolean(),
  filterable: v.boolean(),
  sortable: v.boolean(),
  aggregatable: v.boolean(),
  groupable: v.boolean(),
  creatable: v.boolean(),
  updatable: v.boolean(),
  requiredOnCreate: v.boolean(),
  nullable: v.optional(v.boolean()),
  relationModelLabel: OptionalString,
  relationLabelAxis: OptionalString,
  relationObject: v.nullish(v.boolean()),
});
const Fields = v.pipe(v.array(ResourceFieldSchema), v.readonly());
const RelationAxisSchema = v.looseObject({
  field: v.string(),
  modelLabel: v.string(),
  publicIdField: v.string(),
  labelAxis: OptionalString,
});
const GroupAliasSchema = v.looseObject({
  field: v.string(),
  aggregateField: v.string(),
  aggregateKey: v.string(),
});
const BucketFilterValueMapSchema = v.looseObject({ from: v.unknown(), to: v.unknown() });
const BucketFilterSchema = v.looseObject({
  kind: v.string(),
  field: v.string(),
  valueKey: OptionalString,
  rangeKey: OptionalString,
  lookup: OptionalString,
  nullLookup: OptionalString,
  valueTransform: OptionalString,
  valueMap: v.optional(v.pipe(v.array(BucketFilterValueMapSchema), v.readonly())),
});
const ExtractionSchema = v.looseObject({
  name: v.string(),
  input: v.string(),
  key: v.string(),
  rangeKey: OptionalString,
  filter: v.nullish(BucketFilterSchema),
});
const GroupDimensionSchema = v.looseObject({
  field: v.string(),
  input: v.string(),
  key: v.string(),
  kind: v.string(),
  scalar: OptionalString,
  filter: v.nullish(BucketFilterSchema),
  extractions: v.optional(v.pipe(v.array(ExtractionSchema), v.readonly())),
});
const MeasureSchema = v.looseObject({ op: v.string(), field: OptionalString, input: OptionalString });
const Measures = v.pipe(v.array(MeasureSchema), v.readonly());
const SortSchema = v.looseObject({ field: v.string(), direction: v.string() });
const SelectionPath = v.pipe(
  v.string("must be a string."),
  v.regex(/^[_A-Za-z][_0-9A-Za-z]*(?:\.[_A-Za-z][_0-9A-Za-z]*)*$/, "must be a dotted selection path."),
);
/** Subtitles intentionally expose a closed renderer vocabulary. */
const SubtitleSchema = v.strictObject({
  created: v.nullish(SelectionPath),
  updated: v.nullish(SelectionPath),
  wordCount: v.nullish(SelectionPath),
}, "is not a supported subtitle fact.");
const LinesSchema = v.looseObject({
  field: v.string(),
  modelLabel: v.string(),
  inputType: OptionalString,
  positionField: OptionalString,
  fields: v.optional(Fields),
});
/** Keep extension roots while validating their string/null wire contract. */
const RootsSchema = v.objectWithRest({
  list: OptionalString,
  detail: OptionalString,
  aggregate: OptionalString,
  groups: OptionalString,
  groupsCount: OptionalString,
  create: OptionalString,
  update: OptionalString,
  save: OptionalString,
  delete: OptionalString,
  deletePreview: OptionalString,
  revisions: OptionalString,
  changes: OptionalString,
}, OptionalString);
const TypeNamesSchema = v.objectWithRest({
  query: OptionalString,
  node: OptionalString,
  filter: OptionalString,
  order: OptionalString,
  aggregate: OptionalString,
  grouped: OptionalString,
  groupKey: OptionalString,
  groupBySpec: OptionalString,
  groupOrder: OptionalString,
  having: OptionalString,
  createInput: OptionalString,
  updateInput: OptionalString,
  deletePayload: OptionalString,
  revision: OptionalString,
}, OptionalString);
const ResourceSchema = v.looseObject({
  schemaName: v.string(),
  modelLabel: v.string(),
  resourceType: OptionalString,
  appLabel: v.string(),
  modelName: v.string(),
  canonicalLabel: OptionalString,
  publicIdField: v.string(),
  roots: RootsSchema,
  typeNames: TypeNamesSchema,
  rowModel: v.optional(v.picklist(["client", "server"])),
  recordRepresentation: OptionalString,
  subtitle: v.nullish(SubtitleSchema),
  implFields: v.optional(Strings),
  capabilities: Strings,
  fields: v.optional(Fields),
  filterFields: Strings,
  orderFields: Strings,
  aggregateFields: Strings,
  groupByFields: Strings,
  groupDimensions: v.optional(v.pipe(v.array(GroupDimensionSchema), v.readonly())),
  aggregateMeasures: v.optional(Measures),
  defaultMeasures: v.optional(Measures),
  defaultSort: v.optional(v.pipe(v.array(SortSchema), v.readonly())),
  createFields: v.optional(Strings),
  updateFields: v.optional(Strings),
  requiredCreateFields: v.optional(Strings),
  revisionFields: v.optional(Strings),
  relationAxes: v.pipe(v.array(RelationAxisSchema), v.readonly()),
  groupAliases: v.optional(v.pipe(v.array(GroupAliasSchema), v.readonly())),
  linesResource: v.nullish(LinesSchema),
});
// Anchor the resource output at the envelope boundary so published declarations
// reuse its inferred contract instead of expanding the whole schema graph again.
const ResourceWireSchema: v.GenericSchema<unknown, DataResourceMetadata> = ResourceSchema;
const SchemaMetadataSchema = v.looseObject({
  angee: v.nullish(v.looseObject({
    resources: v.nullish(v.pipe(v.array(ResourceWireSchema), v.readonly())),
  })),
});

export type ModelFieldKind = v.InferOutput<typeof FieldKindSchema>;
export type ModelEnumValueMetadata = v.InferOutput<typeof EnumValueSchema>;
export type DataResourceFieldMetadata = v.InferOutput<typeof ResourceFieldSchema>;
export type DataResourceRelationAxisMetadata = v.InferOutput<typeof RelationAxisSchema>;
export type DataResourceGroupAliasMetadata = v.InferOutput<typeof GroupAliasSchema>;
export type DataResourceGroupBucketFilterValueMapMetadata = v.InferOutput<typeof BucketFilterValueMapSchema>;
export type DataResourceGroupBucketFilterMetadata = v.InferOutput<typeof BucketFilterSchema>;
export type DataResourceGroupExtractionMetadata = v.InferOutput<typeof ExtractionSchema>;
export type DataResourceGroupDimensionMetadata = v.InferOutput<typeof GroupDimensionSchema>;
export type DataResourceAggregateMeasureMetadata = v.InferOutput<typeof MeasureSchema>;
export type DataResourceDefaultSortMetadata = v.InferOutput<typeof SortSchema>;
export type DataResourceSubtitleMetadata = v.InferOutput<typeof SubtitleSchema>;
export type DataResourceLinesMetadata = v.InferOutput<typeof LinesSchema>;
export type DataResourceRootMetadata = v.InferOutput<typeof RootsSchema>;
export type DataResourceTypeMetadata = v.InferOutput<typeof TypeNamesSchema>;
export type DataResourceMetadata = v.InferOutput<typeof ResourceSchema>;
export type AngeeSchemaMetadata = v.InferOutput<typeof SchemaMetadataSchema>;

/** Parse generated metadata once, preserving extension facts and issue paths. */
export function defineAngeeSchemaMetadata(metadata: unknown): AngeeSchemaMetadata {
  const result = v.safeParse(SchemaMetadataSchema, metadata);
  if (result.success) return result.output;
  const issue = result.issues[0];
  const path = issue.path?.map(({ key }) => typeof key === "number" ? `[${key}]` : `.${String(key)}`).join("") ?? "";
  throw new Error(`schema metadata${path} ${issue.message}`);
}
