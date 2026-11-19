import {
  schemaFieldMetadataFromDataResources,
  type DataResourceFieldMetadata,
} from "@angee/metadata";
import {
  testDataResource,
  testQueryField,
  testResourceQuery,
} from "@angee/metadata/testing";
import { expect, test } from "vitest";

import { columnsWithMetadataDefaults } from "./model-metadata-defaults";
import { requestedFieldPaths } from "./resource-view-codecs";

test("archivable rows carry their flag beside identity without a declared column", () => {
  const resource = testDataResource("example.Record", {
    fields: [field("name", "scalar"), field("retired", "scalar", { scalar: "Boolean", archivable: true })],
    query: testResourceQuery({ identity: { field: "public_key" }, fields: {
      name: testQueryField("name"), retired: testQueryField("retired", { scalar: "Boolean" }),
    } }),
  });
  const model = schemaFieldMetadataFromDataResources([resource]).labels[resource.modelLabel]!;
  expect(requestedFieldPaths([{ field: "name" }], undefined, model)).toEqual(["public_key", "retired", "name"]);
  expect(requestedFieldPaths([{ field: "retired" }], ["retired"], model)).toEqual(["public_key", "retired"]);
});

test("trashable rows carry their flag without a declared column or duplicate selections", () => {
  const resource = testDataResource("example.Record", {
    fields: [field("name", "scalar"), field("removed", "scalar", { scalar: "Boolean", trashable: true })],
    query: testResourceQuery({ identity: { field: "public_key" }, fields: {
      name: testQueryField("name"), removed: testQueryField("removed", { scalar: "Boolean" }),
    } }),
  });
  const model = schemaFieldMetadataFromDataResources([resource]).labels[resource.modelLabel]!;
  expect(requestedFieldPaths([{ field: "name" }], undefined, model)).toEqual(["public_key", "removed", "name"]);
  expect(requestedFieldPaths([{ field: "removed" }], ["removed"], model)).toEqual(["public_key", "removed"]);
});

test("archive and trash flags are independent metadata facts, not inferred field names", () => {
  const resource = testDataResource("example.Record", {
    fields: [
      field("retired", "scalar", { scalar: "Boolean", archivable: true }),
      field("removed", "scalar", { scalar: "Boolean", trashable: true }),
      field("is_trashed", "scalar", { scalar: "Boolean" }),
    ],
    query: testResourceQuery({ fields: {
      retired: testQueryField("retired", { scalar: "Boolean" }),
      removed: testQueryField("removed", { scalar: "Boolean" }),
    } }),
  });
  const model = schemaFieldMetadataFromDataResources([resource]).labels[resource.modelLabel]!;
  expect(requestedFieldPaths([], undefined, model)).toEqual(["id", "retired", "removed"]);
});

test.each(["archivable", "trashable"] as const)("an unreadable %s flag is never implicitly selected", (facet) => {
  const resource = testDataResource("example.Record", {
    fields: [field("flag", "scalar", { scalar: "Boolean", [facet]: true, readable: false })],
    query: testResourceQuery({ fields: { flag: testQueryField("flag", { scalar: "Boolean", row: null }) } }),
  });
  for (const candidate of [resource, { ...resource, query: testResourceQuery() }]) {
    const model = schemaFieldMetadataFromDataResources([candidate]).labels[candidate.modelLabel]!;
    expect(requestedFieldPaths([], undefined, model)).toEqual(["id"]);
  }
});

test("archive and trash selections use their declared row paths", () => {
  const resource = testDataResource("example.Record", {
    fields: [field("retired", "scalar", { archivable: true }), field("removed", "scalar", { trashable: true })],
    query: testResourceQuery({ fields: {
      retired: testQueryField("archived_value", { scalar: "Boolean" }),
      removed: testQueryField("trashed_value", { scalar: "Boolean" }),
    } }),
  });
  const model = schemaFieldMetadataFromDataResources([resource]).labels[resource.modelLabel]!;
  expect(requestedFieldPaths([], undefined, model)).toEqual(["id", "archived_value", "trashed_value"]);
});

test("selects native relation representation leaves for resolved sublines", () => {
  const invoice = testDataResource("billing.Invoice", {
    fields: [
      field("amount", "scalar", { scalar: "Decimal", currencyField: "currency" }),
      field("reference", "scalar", { scalar: "String" }),
      field("vendor", "relation", {
        relationModelLabel: "parties.Vendor",
        relationObject: true,
      }),
      field("approvers", "list", {
        scalar: null,
        relationModelLabel: "iam.User",
      }),
      field("currency", "relation", {
        relationModelLabel: "money.Currency",
        relationObject: true,
      }),
    ],
    query: testResourceQuery({
      fields: {
        amount: testQueryField("amount", { scalar: "Decimal" }),
        reference: testQueryField("reference"),
        vendor: testQueryField("vendor.id", {
          kind: "relation",
          scalar: "ID",
          row: { path: "vendor.id", paths: ["vendor.id"] },
          relation: {
            model: "parties.Vendor",
            identityPath: "vendor.id",
            labelPath: "vendor.name",
          },
        }),
        approvers: testQueryField("approvers", {
          kind: "list",
          scalar: null,
          row: null,
        }),
        currency: testQueryField("currency.id", {
          kind: "relation",
          scalar: "ID",
          row: { path: "currency.id", paths: ["currency.id"] },
          relation: {
            model: "money.Currency",
            identityPath: "currency.id",
            labelPath: "currency.code",
          },
        }),
      },
    }),
  });
  const vendor = testDataResource("parties.Vendor", {
    recordRepresentation: "name",
    fields: [field("name", "scalar", { scalar: "String" })],
  });
  const user = testDataResource("iam.User", {
    recordRepresentation: "display_name",
    fields: [field("display_name", "scalar", { scalar: "String" })],
  });
  const currency = testDataResource("money.Currency", {
    recordRepresentation: "code",
    fields: [field("code", "scalar", { scalar: "String" })],
  });
  const schema = schemaFieldMetadataFromDataResources([
    invoice,
    vendor,
    user,
    currency,
  ]);
  const metadata = schema.labels[invoice.modelLabel]!;
  const columns = columnsWithMetadataDefaults(
    [
      { field: "amount", subline: "vendor" },
      { field: "reference", subline: "approvers" },
    ],
    metadata,
    schema,
  );

  expect(columns[0]?.sublineColumn).toMatchObject({
    field: "vendor.name",
    selectionPaths: ["vendor.id", "vendor.name"],
  });
  expect(columns[1]?.sublineColumn).toMatchObject({
    field: "approvers",
    selectionPaths: ["approvers.id", "approvers.display_name"],
    relationList: {
      model: "iam.User",
      identityPath: "id",
      labelPath: "display_name",
    },
  });
  expect(requestedFieldPaths(columns, undefined, metadata)).toEqual([
    "id",
    "amount",
    "vendor.id",
    "vendor.name",
    "reference",
    "approvers.id",
    "approvers.display_name",
  ]);
});

function field(
  name: string,
  kind: DataResourceFieldMetadata["kind"],
  extra: Partial<DataResourceFieldMetadata> = {},
): DataResourceFieldMetadata {
  return {
    name,
    kind,
    readable: true,
    aggregatable: false,
    creatable: false,
    updatable: false,
    requiredOnCreate: false,
    ...extra,
  };
}
