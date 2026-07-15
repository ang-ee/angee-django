import { describe, expect, test } from "vitest";

import {
  refineResourceName,
  refineResourcesFromDataResources,
} from "./resources";
import {
  modelMetadataForLabel,
  schemaFieldMetadataFromDataResources,
} from "./metadata";
import type { DataResourceMetadata } from "./metadata";

describe("refine resource metadata", () => {
  test("uses the Hasura list root as the refine resource name", () => {
    expect(refineResourceName(resource())).toBe("notes");
  });

  test("maps backend resource metadata to refine resources", () => {
    expect(
      refineResourcesFromDataResources([resource()], {
        pathsByResource: { "notes.Note": "/notes" },
      }),
    ).toEqual([
      {
        name: "notes",
        identifier: "console:notes.Note",
        list: "/notes",
        show: "/notes/:id",
        create: "/notes/new",
        edit: "/notes/:id",
        meta: {
          dataProviderName: "console",
          hide: true,
          modelLabel: "notes.Note",
          schemaName: "console",
          resource: resource(),
        },
      },
    ]);
  });

  test("accepts short resource route keys", () => {
    const [mapped] = refineResourcesFromDataResources([resource()], {
      pathsByResource: { Note: "/notes" },
    });

    expect(mapped?.list).toBe("/notes");
  });

  test("keeps a model-label fallback when no explicit node type claims it", () => {
    // A computed `hasura_pydantic_resource` names its node after the pydantic
    // class (`PlatformAddonRow`), not `<Model>Type`; the data view still resolves
    // it by the model label it passes to `useModelMetadata`.
    const computed: DataResourceMetadata = {
      ...resource(),
      modelLabel: "platform.Addon",
      modelName: "Addon",
      typeNames: { node: "PlatformAddonRow" },
    };
    const metadata = schemaFieldMetadataFromDataResources([computed]);

    expect(modelMetadataForLabel(metadata, "platform.Addon")?.resource).toBe(
      computed,
    );
    // The node type name stays addressable too (relation/aggregate joins use it).
    expect(metadata.types.PlatformAddonRow?.resource).toBe(computed);
    expect(metadata.types.AddonType?.resource).toBe(computed);
  });

  test("lets an explicit node type beat another model's label-derived fallback", () => {
    // Regression: distinct model labels stay authoritative even when both model
    // names are Relationship. Their GraphQL node/root names are deliberately
    // disambiguated by the owning addons.
    const { iamRelationships, partyRelationships } = relationshipResources();
    const metadata = schemaFieldMetadataFromDataResources([
      iamRelationships,
      partyRelationships,
    ]);

    expect(metadata.types.RebacRelationshipType?.resource).toBe(
      iamRelationships,
    );
    expect(metadata.types.RelationshipType?.resource).toBe(
      partyRelationships,
    );
    expect(modelMetadataForLabel(metadata, "iam.Relationship")?.resource).toBe(
      iamRelationships,
    );
    expect(
      modelMetadataForLabel(metadata, "parties.Relationship")?.resource,
    ).toBe(partyRelationships);
  });

  test("keeps explicit-over-fallback resolution independent of resource order", () => {
    const { iamRelationships, partyRelationships } = relationshipResources();
    const metadata = schemaFieldMetadataFromDataResources([
      partyRelationships,
      iamRelationships,
    ]);

    expect(metadata.types.RebacRelationshipType?.resource).toBe(
      iamRelationships,
    );
    expect(metadata.types.RelationshipType?.resource).toBe(
      partyRelationships,
    );
    expect(modelMetadataForLabel(metadata, "iam.Relationship")?.resource).toBe(
      iamRelationships,
    );
    expect(
      modelMetadataForLabel(metadata, "parties.Relationship")?.resource,
    ).toBe(partyRelationships);
  });

  test("drops a name claimed by two model-label-derived fallbacks", () => {
    const { iamRelationships } = relationshipResources();
    const crmRelationships: DataResourceMetadata = {
      ...resource(),
      modelLabel: "crm.Relationship",
      appLabel: "crm",
      modelName: "Relationship",
      roots: { ...resource().roots, list: "crm_relationships" },
      typeNames: { node: "CrmRelationshipType" },
    };
    const metadata = schemaFieldMetadataFromDataResources([
      iamRelationships,
      crmRelationships,
    ]);

    expect(metadata.types.RelationshipType).toBeUndefined();
    expect(modelMetadataForLabel(metadata, "iam.Relationship")?.resource).toBe(
      iamRelationships,
    );
    expect(modelMetadataForLabel(metadata, "crm.Relationship")?.resource).toBe(
      crmRelationships,
    );
  });
});

function relationshipResources(): {
  iamRelationships: DataResourceMetadata;
  partyRelationships: DataResourceMetadata;
} {
  return {
    iamRelationships: {
      ...resource(),
      modelLabel: "iam.Relationship",
      appLabel: "iam",
      modelName: "Relationship",
      roots: { ...resource().roots, list: "rebac_relationships" },
      typeNames: { node: "RebacRelationshipType" },
    },
    partyRelationships: {
      ...resource(),
      modelLabel: "parties.Relationship",
      appLabel: "parties",
      modelName: "Relationship",
      roots: { ...resource().roots, list: "relationships" },
      typeNames: { node: "RelationshipType" },
    },
  };
}

function resource(): DataResourceMetadata {
  return {
    schemaName: "console",
    modelLabel: "notes.Note",
    appLabel: "notes",
    modelName: "Note",
    publicIdField: "id",
    roots: {
      list: "notes",
      detail: "notes_by_pk",
      aggregate: "notes_aggregate",
      groups: "notes_groups",
      create: "insert_notes_one",
      update: "update_notes_by_pk",
      delete: "delete_notes_by_pk",
      revisions: "note_revisions",
      changes: "note_changed",
    },
    typeNames: {},
    capabilities: ["list", "detail", "create", "update", "delete"],
    filterFields: ["status"],
    orderFields: ["updated_at"],
    aggregateFields: ["id"],
    groupByFields: ["status"],
    relationAxes: [],
  };
}
