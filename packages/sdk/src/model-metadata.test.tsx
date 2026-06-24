import { describe, expect, test } from "vitest";

import {
  defineAngeeSchemaMetadata,
  fieldMetadataFromSDL,
  modelMetadataForLabel,
  type DataResourceMetadata,
} from "./model-metadata";

const SDL = /* GraphQL */ `
  scalar DateTime

  enum NoteStatus {
    "Draft"
    DRAFT

    "In Review"
    IN_REVIEW

    ACTIVE
  }

  type UserType {
    id: ID!
    displayName: String!
  }

  type NoteType {
    id: ID!
    "Title"
    title: String!
    status: NoteStatus!
    owner: UserType
    reviewers: [UserType!]!
    tags: [String!]!
    createdAt: DateTime!
    isArchived: Boolean!
  }

  type NoteRevision {
    id: ID!
    createdAt: DateTime!
    comment: String
    body: String!
  }

  type Query {
    notes: [NoteType!]!
    noteRevisions(id: ID!): [NoteRevision!]!
  }
`;

describe("defineAngeeSchemaMetadata", () => {
  test("narrows generated JSON metadata after validating the resource shape", () => {
    const generatedKind = "scalar" as string;
    const metadata = defineAngeeSchemaMetadata({
      angee: {
        resources: [
          {
            ...resource({
              modelLabel: "notes.Note",
              node: "NoteType",
              roots: { list: "notes" },
            }),
            fields: [
              {
                ...fieldResource("title"),
                kind: generatedKind,
              },
            ],
          },
        ],
      },
    });

    expect(metadata.angee?.resources?.[0]?.fields?.[0]?.kind).toBe("scalar");
  });

  test("rejects invalid generated field kinds", () => {
    expect(() =>
      defineAngeeSchemaMetadata({
        angee: {
          resources: [
            {
              ...resource({
                modelLabel: "notes.Note",
                node: "NoteType",
                roots: { list: "notes" },
              }),
              fields: [
                {
                  ...fieldResource("title"),
                  kind: "computed",
                },
              ],
            },
          ],
        },
      }),
    ).toThrow(/fields\[0\]\.kind/);
  });
});

describe("fieldMetadataFromSDL", () => {
  const metadata = fieldMetadataFromSDL(SDL);

  test("classifies scalar, enum, relation, and list fields", () => {
    const note = required(metadata.types.NoteType);
    expect(required(note.fields.title)).toMatchObject({
      kind: "scalar",
      scalar: "String",
      label: "Title",
    });
    expect(required(note.fields.status)).toMatchObject({
      kind: "enum",
      enumName: "NoteStatus",
    });
    expect(required(note.fields.owner)).toMatchObject({
      kind: "relation",
      relationTarget: "UserType",
    });
    expect(required(note.fields.reviewers)).toMatchObject({
      kind: "list",
      relationTarget: "UserType",
    });
    expect(required(note.fields.tags)).toMatchObject({
      kind: "list",
      scalar: "String",
    });
    expect(required(note.fields.createdAt)).toMatchObject({
      kind: "scalar",
      scalar: "DateTime",
    });
  });

  test("carries enum values with their SDL description where present", () => {
    const note = required(metadata.types.NoteType);
    expect(required(note.fields.status).values).toEqual([
      { value: "DRAFT", description: "Draft" },
      { value: "IN_REVIEW", description: "In Review" },
      { value: "ACTIVE" },
    ]);
  });

  test("chooses a record representation for model labels", () => {
    expect(required(metadata.types.NoteType).recordRepresentation).toBe("title");
    expect(required(metadata.types.UserType).recordRepresentation).toBe("displayName");
    expect(modelMetadataForLabel(metadata, "notes.Note")).toBe(
      metadata.types.NoteType,
    );
  });

  test("does not infer model roots from raw SDL", () => {
    expect(required(metadata.types.NoteType).rootFields).toBeUndefined();
  });

  test("uses generated resource metadata as authoritative model roots", () => {
    const metadata = fieldMetadataFromSDL(SDL, {
      angee: {
        resources: [
          resource({
            modelLabel: "notes.Note",
            node: "NoteType",
            roots: {
              list: "notes",
              revisions: "noteRevisions",
            },
            typeNames: {
              query: "NoteDataQuery",
              revision: "NoteRevision",
            },
            capabilities: ["list", "revisions"],
            revisionFields: ["createdAt", "comment", "body"],
          }),
        ],
      },
    });

    expect(required(metadata.types.NoteType).rootFields).toEqual({
      list: "notes",
      revisions: "noteRevisions",
      revisionFields: ["createdAt", "comment", "body"],
    });
  });

  test("uses generated resource metadata for writable fields", () => {
    const writeMetadata = fieldMetadataFromSDL(/* GraphQL */ `
      type WidgetType { id: ID! name: String! }
      input WidgetInput { name: String! count: Int color: String! }
      input WidgetPatch { id: ID! name: String count: Int }
      type Query { widget(id: ID!): WidgetType! }
      type Mutation {
        createWidget(data: WidgetInput!): WidgetType!
        updateWidget(data: WidgetPatch!): WidgetType!
      }
    `, {
      angee: {
        resources: [
          resource({
            modelLabel: "widgets.Widget",
            node: "WidgetType",
            roots: {
              detail: "widget",
              create: "createWidget",
              update: "updateWidget",
            },
            typeNames: {
              createInput: "WidgetInput",
              updateInput: "WidgetPatch",
            },
            capabilities: ["detail", "create", "update"],
            createFields: ["name", "count", "color"],
            requiredCreateFields: ["name", "color"],
            updateFields: ["name", "count"],
            fields: [
              fieldResource("name", {
                scalar: "String",
                creatable: true,
                updatable: true,
                requiredOnCreate: true,
              }),
              fieldResource("count", {
                scalar: "Int",
                creatable: true,
                updatable: true,
              }),
              fieldResource("color", {
                scalar: "String",
                creatable: true,
                requiredOnCreate: true,
              }),
            ],
          }),
        ],
      },
    });
    const root = required(writeMetadata.types.WidgetType).rootFields;
    expect(root?.create).toBe("createWidget");
    expect(root?.createFields).toEqual(["name", "count", "color"]);
    expect(root?.requiredCreateFields).toEqual(["name", "color"]);
    expect(root?.update).toBe("updateWidget");
    expect(root?.updateFields).toEqual(["name", "count"]);
    expect(required(writeMetadata.types.WidgetType).fields.name).toMatchObject({
      readable: true,
      creatable: true,
      updatable: true,
      requiredOnCreate: true,
    });
  });

  test("uses generated metadata for irregular create/update/delete-preview roots", () => {
    const writeMetadata = fieldMetadataFromSDL(/* GraphQL */ `
      type DeletePreview { totalDeletedCount: Int! }
      type VcsBridgeType { id: ID! displayName: String! }
      type VcsBridgeTypeOffsetPaginated { results: [VcsBridgeType!]! }
      input VcsBridgeInput { vendor: ID! }
      input VcsBridgePatch { id: ID! webhookSecret: String }
      type Query {
        vcsBridges: VcsBridgeTypeOffsetPaginated!
        vcsBridge(id: ID!): VcsBridgeType
      }
      type Mutation {
        createVcsBridge(data: VcsBridgeInput!): VcsBridgeType!
        updateVcsBridge(data: VcsBridgePatch!): VcsBridgeType!
        delete_vcs_bridges_by_pk(id: String!): VcsBridgeType
        deleteVcsBridge(id: ID!, confirm: Boolean = false): DeletePreview!
      }
    `, {
      angee: {
        resources: [
          resource({
            modelLabel: "integrate.VcsBridge",
            node: "VcsBridgeType",
            roots: {
              list: "vcsBridges",
              detail: "vcsBridge",
              create: "createVcsBridge",
              update: "updateVcsBridge",
              delete: "delete_vcs_bridges_by_pk",
              deletePreview: "deleteVcsBridge",
            },
            typeNames: {
              query: "VcsBridgeDataQuery",
              createInput: "VcsBridgeInput",
              updateInput: "VcsBridgePatch",
              deletePayload: "DeletePreview",
            },
            capabilities: ["list", "detail", "create", "update", "delete", "deletePreview"],
            createFields: ["vendor"],
            updateFields: ["webhookSecret"],
          }),
        ],
      },
    });

    expect(
      required(modelMetadataForLabel(writeMetadata, "integrate.VcsBridge")).rootFields,
    ).toMatchObject({
      detail: "vcsBridge",
      list: "vcsBridges",
      create: "createVcsBridge",
      createFields: ["vendor"],
      update: "updateVcsBridge",
      updateFields: ["webhookSecret"],
      delete: "delete_vcs_bridges_by_pk",
      deletePreview: "deleteVcsBridge",
    });
  });

  test("uses generated metadata for grouped aggregate roots", () => {
    const metadata = fieldMetadataFromSDL(/* GraphQL */ `
      type IntegrationType { id: ID! status: String! }
      type NoteType { id: ID! title: String! }
      input IntegrationFilter { status: String }
      input NoteFilter { title: String }
      input IntegrationAggregateGroupBySpec { field: String! }
      input IntegrationAggregateGroupOrder { field: String! }
      input NoteGroupBySpec { field: String! }
      type IntegrationAggregateAggregate { count: Int! }
      type IntegrationGroupKey { status: String }
      type IntegrationAggregateGrouped {
        key: IntegrationGroupKey!
        aggregate: IntegrationAggregateAggregate!
      }
      type NoteAggregate { count: Int! }
      type NoteGroupKey { title: String }
      type NoteGrouped { key: NoteGroupKey! aggregate: NoteAggregate! }
      type Query {
        integrations: [IntegrationType!]!
        notes: [NoteType!]!
        integrationAggregate(filter: IntegrationFilter = null): IntegrationAggregateAggregate!
        integrationGroups(groupBy: [IntegrationAggregateGroupBySpec!]!, filter: IntegrationFilter = null, orderBy: [IntegrationAggregateGroupOrder!] = null): [IntegrationAggregateGrouped!]!
        noteGroups(groupBy: [NoteGroupBySpec!]!, filter: NoteFilter = null): [NoteGrouped!]!
      }
    `, {
      angee: {
        resources: [
          resource({
            modelLabel: "integrate.Integration",
            node: "IntegrationType",
            roots: {
              list: "integrations",
              aggregate: "integrationAggregate",
              groups: "integrationGroups",
            },
            typeNames: {
              query: "IntegrationDataQuery",
              filter: "IntegrationFilter",
              groupBySpec: "IntegrationAggregateGroupBySpec",
              groupKey: "IntegrationGroupKey",
              groupOrder: "IntegrationAggregateGroupOrder",
            },
            capabilities: ["list", "aggregate", "groups"],
          }),
          resource({
            modelLabel: "notes.Note",
            node: "NoteType",
            roots: {
              list: "notes",
              groups: "noteGroups",
            },
            typeNames: {
              query: "NoteDataQuery",
              filter: "NoteFilter",
              groupBySpec: "NoteGroupBySpec",
              groupKey: "NoteGroupKey",
            },
            capabilities: ["list", "groups"],
          }),
        ],
      },
    });

    expect(required(metadata.types.IntegrationType).rootFields).toMatchObject({
      list: "integrations",
      aggregate: "integrationAggregate",
      groupBy: "integrationGroups",
      groupByInput: "IntegrationAggregateGroupBySpec",
      groupOrderInput: "IntegrationAggregateGroupOrder",
    });
    expect(required(metadata.types.NoteType).rootFields).toMatchObject({
      list: "notes",
      groupBy: "noteGroups",
      groupByInput: "NoteGroupBySpec",
    });
  });

  test("uses generated resource type names for relation filter contracts", () => {
    const metadata = fieldMetadataFromSDL(/* GraphQL */ `
      type ProviderType { id: ID! name: String! }
      type ModelType {
        id: ID!
        provider: ProviderType
        publisher: ProviderType
        drive: ProviderType
      }
      type ModelTypeOffsetPaginated { results: [ModelType!]! }
      input DjangoModelFilterInput { sqid: ID! }
      input ModelFilter {
        provider: DjangoModelFilterInput
        publisher: DjangoModelFilterInput
        drive: DjangoModelFilterInput
      }
      input ModelAggregateGroupBySpec { field: String! }
      type ModelAggregateGroupKey { providerId: ID publisher: ID }
      type ModelAggregateGrouped { key: ModelAggregateGroupKey! count: Int! }
      type ModelAggregateGroupedResult { results: [ModelAggregateGrouped!]! }
      type Query {
        models(filters: ModelFilter): ModelTypeOffsetPaginated!
        modelGroups(groupBy: [ModelAggregateGroupBySpec!]!, filter: ModelFilter): ModelAggregateGroupedResult!
      }
    `, {
      angee: {
        resources: [
          resource({
            modelLabel: "demo.Model",
            node: "ModelType",
            roots: {
              list: "models",
              groups: "modelGroups",
            },
            typeNames: {
              query: "ModelDataQuery",
              filter: "ModelFilter",
              groupBySpec: "ModelAggregateGroupBySpec",
              groupKey: "ModelAggregateGroupKey",
            },
            capabilities: ["list", "groups"],
            filterFields: ["provider", "publisher", "drive"],
            groupByFields: ["provider", "publisher"],
          }),
        ],
      },
    });

    const fields = required(metadata.types.ModelType).fields;
    expect(required(fields.provider).relationFilter).toEqual({
      field: "provider",
      mode: "lookup",
      lookup: "sqid",
      aggregateKey: "providerId",
    });
    expect(required(fields.publisher).relationFilter).toEqual({
      field: "publisher",
      mode: "lookup",
      lookup: "sqid",
      aggregateKey: "publisher",
    });
    expect(required(fields.drive).relationFilter).toEqual({
      field: "drive",
      mode: "lookup",
      lookup: "sqid",
    });
  });

  test("uses generated resource type names for relation label axes", () => {
    const metadata = fieldMetadataFromSDL(/* GraphQL */ `
      type ProviderType { id: ID! name: String! }
      type ModelType { id: ID! provider: ProviderType ambiguous: ProviderType }
      type ModelTypeOffsetPaginated { results: [ModelType!]! }
      input DjangoModelFilterInput { sqid: ID! }
      input ModelFilter { provider: DjangoModelFilterInput ambiguous: DjangoModelFilterInput }
      input ModelAggregateGroupBySpec { field: String! }
      type ModelAggregateGroupKey {
        providerId: ID
        provider_DisplayName: String
        ambiguousId: ID
        ambiguous_DisplayName: String
        ambiguous_Email: String
      }
      type ModelAggregateGrouped { key: ModelAggregateGroupKey! count: Int! }
      type ModelAggregateGroupedResult { results: [ModelAggregateGrouped!]! }
      type Query {
        models(filters: ModelFilter): ModelTypeOffsetPaginated!
        modelGroups(groupBy: [ModelAggregateGroupBySpec!]!, filter: ModelFilter): ModelAggregateGroupedResult!
      }
    `, {
      angee: {
        resources: [
          resource({
            modelLabel: "demo.Model",
            node: "ModelType",
            roots: {
              list: "models",
              groups: "modelGroups",
            },
            typeNames: {
              query: "ModelDataQuery",
              filter: "ModelFilter",
              groupBySpec: "ModelAggregateGroupBySpec",
              groupKey: "ModelAggregateGroupKey",
            },
            capabilities: ["list", "groups"],
            filterFields: ["provider", "ambiguous"],
            groupByFields: ["provider", "provider_DisplayName", "ambiguous", "ambiguous_DisplayName", "ambiguous_Email"],
          }),
        ],
      },
    });

    const fields = required(metadata.types.ModelType).fields;
    // Exactly one `provider_*` group-key leaf → that is the display-label axis.
    expect(required(fields.provider).relationFilter?.labelKey).toBe("provider_DisplayName");
    // Two `ambiguous_*` leaves → ambiguous, so no label axis (group labels by id).
    expect(required(fields.ambiguous).relationFilter?.labelKey).toBeUndefined();
  });

  test("uses generated resource metadata as authoritative model roots", () => {
    const metadata = fieldMetadataFromSDL(
      /* GraphQL */ `
        type IntegrationType { id: ID! status: String! }
        type IntegrationAggregateAggregate { count: Int! }
        input IntegrationAggregateGroupBySpec { field: String! }
        type IntegrationGroupKey { status: String }
        type IntegrationAggregateGrouped {
          key: IntegrationGroupKey!
          aggregate: IntegrationAggregateAggregate!
        }
        type Query {
          integrations: [IntegrationType!]!
          integration(id: ID!): IntegrationType
          integrationAggregate: IntegrationAggregateAggregate!
          integrationGroups(groupBy: [IntegrationAggregateGroupBySpec!]!): [IntegrationAggregateGrouped!]!
        }
      `,
      {
        angee: {
          resources: [
            {
              schemaName: "public",
              modelLabel: "integrate.Integration",
              appLabel: "integrate",
              modelName: "integration",
              publicIdField: "sqid",
              roots: {
                list: "integrations",
                detail: "integration",
                aggregate: "integrationAggregate",
                groups: "integrationGroups",
              },
              typeNames: {
                query: "IntegrationDataQuery",
                node: "IntegrationType",
                groupBySpec: "IntegrationAggregateGroupBySpec",
                groupKey: "IntegrationGroupKey",
              },
              capabilities: ["list", "detail", "aggregate", "groups"],
              filterFields: ["vendor", "status"],
              orderFields: ["status"],
              aggregateFields: ["id"],
              groupByFields: ["vendor", "status"],
              relationAxes: [],
            },
          ],
        },
      },
    );

    const integration = required(modelMetadataForLabel(metadata, "integrate.Integration"));
    expect(integration.rootFields).toMatchObject({
      detail: "integration",
      list: "integrations",
      aggregate: "integrationAggregate",
      groupBy: "integrationGroups",
      groupByInput: "IntegrationAggregateGroupBySpec",
    });
    expect(integration.resource?.modelLabel).toBe("integrate.Integration");
    expect(metadata.resources?.[0]?.modelLabel).toBe("integrate.Integration");
  });

  test("fails fast when generated metadata drifts from the SDL", () => {
    expect(() =>
      fieldMetadataFromSDL(
        /* GraphQL */ `
          type IntegrationType { id: ID! status: String! }
          type Query { integrations: [IntegrationType!]! }
        `,
        {
          angee: {
            resources: [
              {
                schemaName: "public",
                modelLabel: "integrate.Integration",
                appLabel: "integrate",
                modelName: "integration",
                publicIdField: "sqid",
                roots: {
                  list: "integrations",
                  groups: "missingGroups",
                },
                typeNames: {
                  query: "IntegrationDataQuery",
                  node: "IntegrationType",
                },
                capabilities: ["list", "groups"],
                filterFields: [],
                orderFields: [],
                aggregateFields: ["id"],
                groupByFields: ["status"],
                relationAxes: [],
              },
            ],
          },
        },
      ),
    ).toThrow(/missing Query field "missingGroups"/);
  });

  test("uses generated relation axes for public-id lookup filters", () => {
    const metadata = fieldMetadataFromSDL(
      /* GraphQL */ `
        type ProviderType { id: ID! name: String! }
        type ModelType {
          id: ID!
          provider: ProviderType
          implCategory: String!
          implClass: String!
        }
        type ModelTypeOffsetPaginated { results: [ModelType!]! }
        input LegacyRelationFilterInput { pk: ID! }
        input ModelFilter { provider: LegacyRelationFilterInput }
        input ModelAggregateGroupBySpec { field: String! }
      type ModelAggregateGroupKey {
        providerId: ID
        provider_DisplayName: String
        implClass: String
      }
        type ModelAggregateGrouped { key: ModelAggregateGroupKey! count: Int! }
        type ModelAggregateGroupedResult { results: [ModelAggregateGrouped!]! }
        type Query {
          models(filters: ModelFilter): ModelTypeOffsetPaginated!
          modelGroups(groupBy: [ModelAggregateGroupBySpec!]!, filter: ModelFilter): ModelAggregateGroupedResult!
        }
      `,
      {
        angee: {
          resources: [
            {
              schemaName: "public",
              modelLabel: "demo.Model",
              appLabel: "demo",
              modelName: "model",
              publicIdField: "sqid",
              roots: {
                list: "models",
                groups: "modelGroups",
              },
              typeNames: {
                query: "ModelDataQuery",
                node: "ModelType",
                filter: "ModelFilter",
                groupBySpec: "ModelAggregateGroupBySpec",
                groupKey: "ModelAggregateGroupKey",
              },
              capabilities: ["list", "groups", "filterEcho"],
              filterFields: ["provider"],
              orderFields: ["implClass"],
              aggregateFields: ["id"],
              groupByFields: ["provider", "implClass"],
              groupDimensions: [
                {
                  field: "provider",
                  input: "PROVIDER",
                  key: "providerId",
                  kind: "relation",
                  scalar: "ID",
                },
                {
                  field: "implClass",
                  input: "IMPL_CLASS",
                  key: "implClass",
                  kind: "column",
                  scalar: "String",
                },
              ],
              defaultMeasures: [{ op: "count" }],
              defaultSort: [{ field: "implClass", direction: "ASC" }],
              relationAxes: [
                {
                  field: "provider",
                  modelLabel: "demo.Provider",
                  publicIdField: "sqid",
                  labelAxis: "provider_DisplayName",
                },
              ],
              groupAliases: [
                {
                  field: "implCategory",
                  aggregateField: "implClass",
                  aggregateKey: "implClass",
                },
              ],
            },
          ],
        },
      },
    );

    expect(required(metadata.types.ModelType).fields.provider?.relationFilter).toEqual({
      field: "provider",
      mode: "lookup",
      lookup: "sqid",
      aggregateKey: "providerId",
      labelKey: "provider_DisplayName",
    });
    expect(required(metadata.types.ModelType).resource?.groupAliases).toEqual([
      {
        field: "implCategory",
        aggregateField: "implClass",
        aggregateKey: "implClass",
      },
    ]);
    expect(required(metadata.types.ModelType).resource?.groupDimensions).toEqual([
      {
        field: "provider",
        input: "PROVIDER",
        key: "providerId",
        kind: "relation",
        scalar: "ID",
      },
      {
        field: "implClass",
        input: "IMPL_CLASS",
        key: "implClass",
        kind: "column",
        scalar: "String",
      },
    ]);
    expect(required(metadata.types.ModelType).resource?.defaultMeasures).toEqual([
      { op: "count" },
    ]);
    expect(required(metadata.types.ModelType).resource?.defaultSort).toEqual([
      { field: "implClass", direction: "ASC" },
    ]);
  });

  test("rejects generated default sort fields missing from order metadata", () => {
    expect(() =>
      fieldMetadataFromSDL(
        /* GraphQL */ `
          type ModelType { id: ID! status: String! }
          type Query { models: [ModelType!]! }
        `,
        {
          angee: {
            resources: [
              resource({
                modelLabel: "demo.Model",
                node: "ModelType",
                roots: { list: "models" },
                typeNames: { query: "ModelDataQuery" },
                capabilities: ["list"],
                orderFields: ["status"],
                defaultSort: [{ field: "missing", direction: "ASC" }],
              }),
            ],
          },
        },
      )
    ).toThrow(/default sort field "missing", but it is not sortable/);
  });

  test("rejects generated group dimensions missing from the group key type", () => {
    expect(() =>
      fieldMetadataFromSDL(
        /* GraphQL */ `
          type ModelType { id: ID! status: String! }
          input ModelAggregateGroupBySpec { field: String! }
          type ModelAggregateGroupKey { status: String }
          type ModelAggregateGrouped { key: ModelAggregateGroupKey! count: Int! }
          type ModelAggregateGroupedResult { results: [ModelAggregateGrouped!]! }
          type Query {
            models: [ModelType!]!
            modelGroups(groupBy: [ModelAggregateGroupBySpec!]!): ModelAggregateGroupedResult!
          }
        `,
        {
          angee: {
            resources: [
              resource({
                modelLabel: "demo.Model",
                node: "ModelType",
                roots: {
                  list: "models",
                  groups: "modelGroups",
                },
                typeNames: {
                  query: "ModelDataQuery",
                  groupBySpec: "ModelAggregateGroupBySpec",
                  groupKey: "ModelAggregateGroupKey",
                },
                capabilities: ["list", "groups"],
                groupByFields: ["status"],
                groupDimensions: [
                  {
                    field: "status",
                    input: "STATUS",
                    key: "missingStatus",
                    kind: "column",
                    scalar: "String",
                  },
                ],
              }),
            ],
          },
        },
      )
    ).toThrow(/missing group key field "missingStatus"/);
  });

  test("accepts typed Hasura/NDC group key metadata", () => {
    const metadata = fieldMetadataFromSDL(
      /* GraphQL */ `
        type ModelType { id: ID! status: String! updated_at: String! }
        enum Granularity { MONTH }
        enum ModelGroupableField { STATUS UPDATED_AT }
        input ModelGroupBySpec { field: ModelGroupableField! granularity: Granularity }
        type BucketRange { from: String! to: String! }
        type ModelGroupKey {
          status: String
          updated_at: String
          updated_at_month: String
          updated_at_month_range: BucketRange
        }
        type models_aggregate_fields { count: Int! }
        type models_group {
          key: ModelGroupKey!
          aggregate: models_aggregate_fields!
        }
        type Query {
          models: [ModelType!]!
          models_groups(group_by: [ModelGroupBySpec!]!): [models_group!]!
        }
      `,
      {
        angee: {
          resources: [
            resource({
              modelLabel: "demo.Model",
              node: "ModelType",
              roots: {
                list: "models",
                groups: "models_groups",
              },
              typeNames: {
                query: "models_Query",
                groupBySpec: "ModelGroupBySpec",
                groupKey: "ModelGroupKey",
                grouped: "models_group",
              },
              capabilities: ["list", "groups"],
              groupByFields: ["status", "updated_at"],
              groupDimensions: [
                {
                  field: "status",
                  input: "STATUS",
                  key: "status",
                  kind: "column",
                  scalar: "String",
                },
                {
                  field: "updated_at",
                  input: "UPDATED_AT",
                  key: "updated_at",
                  kind: "column",
                  scalar: "DateTime",
                  extractions: [
                    {
                      input: "MONTH",
                      key: "updated_at_month",
                      rangeKey: "updated_at_month_range",
                      name: "month",
                    },
                  ],
                },
              ],
            }),
          ],
        },
      },
    );

    expect(required(metadata.types.ModelType).resource?.groupDimensions).toEqual([
      {
        field: "status",
        input: "STATUS",
        key: "status",
        kind: "column",
        scalar: "String",
      },
      {
        field: "updated_at",
        input: "UPDATED_AT",
        key: "updated_at",
        kind: "column",
        scalar: "DateTime",
        extractions: [
          {
            input: "MONTH",
            key: "updated_at_month",
            rangeKey: "updated_at_month_range",
            name: "month",
          },
        ],
      },
    ]);
  });

  test("rejects grouped resources missing their typed group key", () => {
    expect(() =>
      fieldMetadataFromSDL(
        /* GraphQL */ `
          type ModelType { id: ID! status: String! }
          enum ModelGroupableField { STATUS }
          input ModelGroupBySpec { field: ModelGroupableField! }
          type models_aggregate_fields { count: Int! }
          type models_group { key: String! aggregate: models_aggregate_fields! }
          type Query {
            models: [ModelType!]!
            models_groups(group_by: [ModelGroupBySpec!]!): [models_group!]!
          }
        `,
        {
          angee: {
            resources: [
              resource({
                modelLabel: "demo.Model",
                node: "ModelType",
                roots: {
                  list: "models",
                  groups: "models_groups",
                },
                typeNames: {
                  query: "models_Query",
                  groupBySpec: "ModelGroupBySpec",
                  groupKey: "MissingGroupKey",
                },
                capabilities: ["list", "groups"],
                groupByFields: ["status"],
                groupDimensions: [
                  {
                    field: "status",
                    input: "STATUS",
                    key: "status",
                    kind: "column",
                    scalar: "String",
                  },
                ],
              }),
            ],
          },
        },
      )
    ).toThrow(/missing object type "MissingGroupKey"/);
  });

  test("rejects generated group aliases missing from the node type", () => {
    expect(() =>
      fieldMetadataFromSDL(
        /* GraphQL */ `
          type ModelType { id: ID! implClass: String! }
          type Query { models: [ModelType!]! }
        `,
        {
          angee: {
            resources: [
              {
                schemaName: "public",
                modelLabel: "demo.Model",
                appLabel: "demo",
                modelName: "model",
                publicIdField: "sqid",
                roots: { list: "models" },
                typeNames: {
                  query: "ModelDataQuery",
                  node: "ModelType",
                },
                capabilities: ["list", "groups"],
                filterFields: [],
                orderFields: [],
                aggregateFields: ["id"],
                groupByFields: ["implClass"],
                relationAxes: [],
                groupAliases: [
                  {
                    field: "implCategory",
                    aggregateField: "implClass",
                    aggregateKey: "implClass",
                  },
                ],
              },
            ],
          },
        },
      ),
    ).toThrow(/does not expose that field/);
  });
});

function required<T>(value: T | null | undefined): T {
  if (value == null) throw new Error("Expected fixture value to exist.");
  return value;
}

type ResourceFixture = Omit<
  Partial<DataResourceMetadata>,
  "roots" | "typeNames"
> & {
  modelLabel: string;
  node: string;
  roots?: DataResourceMetadata["roots"];
  typeNames?: Partial<DataResourceMetadata["typeNames"]>;
};

function resource({
  node,
  roots = {},
  typeNames = {},
  ...metadata
}: ResourceFixture): DataResourceMetadata {
  const [appLabel = "", objectName = ""] = metadata.modelLabel.split(".");
  return {
    schemaName: "public",
    appLabel,
    modelName: objectName.toLowerCase(),
    publicIdField: "sqid",
    capabilities: [],
    filterFields: [],
    orderFields: [],
    aggregateFields: [],
    groupByFields: [],
    relationAxes: [],
    ...metadata,
    roots,
    typeNames: {
      node,
      ...typeNames,
    },
  };
}

function fieldResource(
  name: string,
  metadata: Partial<NonNullable<DataResourceMetadata["fields"]>[number]> = {},
): NonNullable<DataResourceMetadata["fields"]>[number] {
  return {
    name,
    kind: "scalar",
    readable: true,
    filterable: false,
    sortable: false,
    aggregatable: false,
    groupable: false,
    creatable: false,
    updatable: false,
    requiredOnCreate: false,
    ...metadata,
  };
}
