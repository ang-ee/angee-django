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
      type Query { widget(id: ID!): WidgetType! }
      type Mutation { createWidget(data: WidgetInput!): WidgetType! }
    `);
    const root = required(writeMetadata.types.WidgetType).rootFields;
    expect(root?.create).toBe("createWidget");
    expect(root?.requiredCreateFields).toEqual(["name", "color"]);
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
});

function required<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("Expected fixture value to exist.");
  return value;
}
