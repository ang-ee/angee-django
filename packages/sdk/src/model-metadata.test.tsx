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

  type Query {
    notes: [NoteType!]!
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

  test("derives enum labels from enum-value descriptions with a fallback", () => {
    const note = required(metadata.types.NoteType);
    expect(required(note.fields.status).values).toEqual([
      { value: "DRAFT", label: "Draft" },
      { value: "IN_REVIEW", label: "In Review" },
      { value: "ACTIVE", label: "Active" },
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
    });
  });
});

function required<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("Expected fixture value to exist.");
  return value;
}
