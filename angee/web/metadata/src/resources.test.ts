import { describe, expect, test } from "vitest";

import {
  refineResourceName,
  refineResourcesFromDataResources,
} from "./resources";
import {
  modelMetadataForLabel,
  relationFilterForRelation,
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

describe("relation contract, whichever way the node projects the FK", () => {
  // `drive` is a to-one relation the node projects as a bare `ID` scalar;
  // `oauth_client` is one the node does not project at all (a curated node shows
  // derived `provider_*` columns instead). Both are declared relation axes.
  const relationResource = (): DataResourceMetadata => ({
    ...resource(),
    modelLabel: "storage.File",
    typeNames: { node: "FileType" },
    filterFields: ["drive", "oauth_client"],
    groupByFields: ["drive", "drive__name", "oauth_client", "oauth_client__display_name"],
    fields: [
      {
        name: "drive",
        kind: "scalar",
        scalar: "ID",
        relationModelLabel: "storage.Drive",
        relationLabelAxis: "drive__name",
        relationObject: false,
        readable: true,
        filterable: true,
        groupable: true,
        sortable: false,
        aggregatable: false,
        creatable: false,
        updatable: false,
        archivable: false,
        requiredOnCreate: false,
        values: [],
      },
    ] as unknown as DataResourceMetadata["fields"],
    groupDimensions: [
      { field: "drive", input: "DRIVE", key: "drive_id", kind: "relation", scalar: "ID" },
      { field: "drive__name", input: "DRIVE__NAME", key: "drive__name", kind: "column", scalar: "String" },
      { field: "oauth_client", input: "OAUTH_CLIENT", key: "oauth_client_id", kind: "relation", scalar: "ID" },
    ] as unknown as DataResourceMetadata["groupDimensions"],
    relationAxes: [
      { field: "drive", modelLabel: "storage.Drive", publicIdField: "sqid", labelAxis: "drive__name" },
      { field: "oauth_client", modelLabel: "integrate.OAuthClient", publicIdField: "sqid", labelAxis: "oauth_client__display_name" },
    ],
  });

  test("a relation projected as a bare ID scalar still carries its relation filter", () => {
    const metadata = schemaFieldMetadataFromDataResources([relationResource()]);
    const model = modelMetadataForLabel(metadata, "storage.File");
    expect(model?.fields.drive?.relationFilter).toEqual({
      field: "drive",
      mode: "lookup",
      lookup: "sqid",
      aggregateKey: "drive_id",
      labelKey: "drive__name",
    });
  });

  test("a relation the node never projects still resolves through its axis", () => {
    const metadata = schemaFieldMetadataFromDataResources([relationResource()]);
    const model = modelMetadataForLabel(metadata, "storage.File");
    // No node field exists for it at all...
    expect(model?.fields.oauth_client).toBeUndefined();
    // ...but the axis still owns the identity and the label.
    expect(relationFilterForRelation("oauth_client", model)).toEqual({
      field: "oauth_client",
      mode: "lookup",
      lookup: "sqid",
      aggregateKey: "oauth_client_id",
      labelKey: "oauth_client__display_name",
    });
  });

  test("a field no axis names gets no relation filter", () => {
    const metadata = schemaFieldMetadataFromDataResources([relationResource()]);
    const model = modelMetadataForLabel(metadata, "storage.File");
    expect(relationFilterForRelation("status", model)).toBeUndefined();
  });
});
