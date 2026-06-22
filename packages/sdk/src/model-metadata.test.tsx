import { describe, expect, test } from "vitest";

import {
  fieldMetadataFromSDL,
  modelMetadataForLabel,
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

  test("captures schema-declared root fields for model types", () => {
    expect(required(metadata.types.NoteType).rootFields).toEqual({
      list: "notes",
      revisions: "noteRevisions",
      revisionFields: ["createdAt", "comment", "body"],
    });
  });

  test("captures the create input's required (non-null, no-default) fields", () => {
    const writeMetadata = fieldMetadataFromSDL(/* GraphQL */ `
      type WidgetType { id: ID! name: String! }
      input WidgetInput { name: String! count: Int color: String! }
      input WidgetPatch { id: ID! name: String count: Int }
      type Query { widget(id: ID!): WidgetType! }
      type Mutation {
        createWidget(data: WidgetInput!): WidgetType!
        updateWidget(data: WidgetPatch!): WidgetType!
      }
    `);
    const root = required(writeMetadata.types.WidgetType).rootFields;
    expect(root?.create).toBe("createWidget");
    expect(root?.createFields).toEqual(["name", "count", "color"]);
    expect(root?.requiredCreateFields).toEqual(["name", "color"]);
    expect(root?.update).toBe("updateWidget");
    expect(root?.updateFields).toEqual(["name", "count"]);
  });

  test("matches conventional delete roots for generated model names", () => {
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
        deleteVcsBridge(id: ID!, confirm: Boolean = false): DeletePreview!
      }
    `);

    expect(
      required(modelMetadataForLabel(writeMetadata, "integrate.VcsBridge")).rootFields,
    ).toMatchObject({
      detail: "vcsBridge",
      list: "vcsBridges",
      create: "createVcsBridge",
      createFields: ["vendor"],
      update: "updateVcsBridge",
      updateFields: ["webhookSecret"],
      delete: "deleteVcsBridge",
    });
  });

  test("captures grouped aggregate roots with prefixed aggregate types", () => {
    const metadata = fieldMetadataFromSDL(/* GraphQL */ `
      type IntegrationType { id: ID! status: String! }
      type NoteType { id: ID! title: String! }
      input IntegrationFilter { status: String }
      input NoteFilter { title: String }
      input IntegrationAggregateGroupBySpec { field: String! }
      input IntegrationAggregateGroupOrder { field: String! }
      input NoteGroupBySpec { field: String! }
      type IntegrationAggregateAggregate { count: Int! }
      type IntegrationAggregateGrouped { key: String count: Int! }
      type IntegrationAggregateGroupedResult {
        results: [IntegrationAggregateGrouped!]!
      }
      type NoteGrouped { key: String count: Int! }
      type NoteGroupedResult { results: [NoteGrouped!]! }
      type Query {
        integrations: [IntegrationType!]!
        notes: [NoteType!]!
        integrationAggregate(filter: IntegrationFilter = null): IntegrationAggregateAggregate!
        integrationGroups(groupBy: [IntegrationAggregateGroupBySpec!]!, filter: IntegrationFilter = null, orderBy: [IntegrationAggregateGroupOrder!] = null): IntegrationAggregateGroupedResult!
        noteGroups(groupBy: [NoteGroupBySpec!]!, filter: NoteFilter = null): NoteGroupedResult!
      }
    `);

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

  test("captures relation filter contracts from filter inputs", () => {
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
    `);

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

  test("captures a relation's label axis (one leaf) and skips it when ambiguous", () => {
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
    `);

    const fields = required(metadata.types.ModelType).fields;
    // Exactly one `provider_*` group-key leaf → that is the display-label axis.
    expect(required(fields.provider).relationFilter?.labelKey).toBe("provider_DisplayName");
    // Two `ambiguous_*` leaves → ambiguous, so no label axis (group labels by id).
    expect(required(fields.ambiguous).relationFilter?.labelKey).toBeUndefined();
  });

  test("uses generated data-query metadata as authoritative model roots", () => {
    const metadata = fieldMetadataFromSDL(
      /* GraphQL */ `
        type IntegrationType { id: ID! status: String! }
        type IntegrationAggregateAggregate { count: Int! }
        input IntegrationAggregateGroupBySpec { field: String! }
        type IntegrationAggregateGrouped { key: String count: Int! }
        type IntegrationAggregateGroupedResult {
          results: [IntegrationAggregateGrouped!]!
        }
        type Query {
          integrations: [IntegrationType!]!
          integration(id: ID!): IntegrationType
          integrationAggregate: IntegrationAggregateAggregate!
          integrationGroups(groupBy: [IntegrationAggregateGroupBySpec!]!): IntegrationAggregateGroupedResult!
        }
      `,
      {
        angee: {
          dataQueries: [
            {
              modelLabel: "integrate.Integration",
              appLabel: "integrate",
              modelName: "integration",
              publicIdField: "sqid",
              roots: {
                listName: "integrations",
                detailName: "integration",
                aggregateName: "integrationAggregate",
                groupName: "integrationGroups",
              },
              typeNames: {
                query: "IntegrationDataQuery",
                node: "IntegrationType",
                groupBySpec: "IntegrationAggregateGroupBySpec",
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
    expect(integration.dataQuery?.modelLabel).toBe("integrate.Integration");
    expect(metadata.dataQueries?.[0]?.modelLabel).toBe("integrate.Integration");
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
            dataQueries: [
              {
                modelLabel: "integrate.Integration",
                appLabel: "integrate",
                modelName: "integration",
                publicIdField: "sqid",
                roots: {
                  listName: "integrations",
                  groupName: "missingGroups",
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
          dataQueries: [
            {
              modelLabel: "demo.Model",
              appLabel: "demo",
              modelName: "model",
              publicIdField: "sqid",
              roots: {
                listName: "models",
                groupName: "modelGroups",
              },
              typeNames: {
                query: "ModelDataQuery",
                node: "ModelType",
                filter: "ModelFilter",
                groupBySpec: "ModelAggregateGroupBySpec",
              },
              capabilities: ["list", "groups", "filterEcho"],
              filterFields: ["provider"],
              orderFields: [],
              aggregateFields: ["id"],
              groupByFields: ["provider", "implClass"],
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
    expect(required(metadata.types.ModelType).dataQuery?.groupAliases).toEqual([
      {
        field: "implCategory",
        aggregateField: "implClass",
        aggregateKey: "implClass",
      },
    ]);
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
            dataQueries: [
              {
                modelLabel: "demo.Model",
                appLabel: "demo",
                modelName: "model",
                publicIdField: "sqid",
                roots: { listName: "models" },
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
