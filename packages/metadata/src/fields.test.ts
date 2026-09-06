import { testDataResource } from "./testing";
import { resourceOrderFieldForPath } from "./fields";
import { describe, expect, test } from "vitest";

import {
  defaultWidgetForModelField,
  fieldUpdatable,
  filterFieldType,
  isScalarIdRelation,
} from "./fields";
import type {
  DataResourceFieldMetadata,
  ModelMetadata,
} from "./artifact";
import { schemaFieldMetadataFromDataResources } from "./artifact";

describe("field metadata helpers", () => {
  test("treats Decimal as a number field with the numeric widget", () => {
    const field = resourceField({
      name: "amount",
      kind: "scalar",
      scalar: "Decimal",
    });

    expect(defaultWidgetForModelField(field)).toBe("float");
    expect(filterFieldType("amount", field)).toBe("number");
  });

  test("resolves a money field to the money widget, filtering as a number", () => {
    const field = resourceField({
      name: "amount",
      kind: "scalar",
      scalar: "Decimal",
      widget: "money",
      currencyField: "currency",
    });

    expect(defaultWidgetForModelField(field)).toBe("money");
    expect(filterFieldType("amount", field)).toBe("number");
  });

  test("uses date-name inference only when field metadata is absent", () => {
    const stringField = resourceField({
      name: "published_at",
      kind: "scalar",
      scalar: "String",
    });

    expect(filterFieldType("published_at", undefined)).toBe("datetime");
    expect(filterFieldType("published_at", stringField)).toBe("text");
  });

  test("recognizes finalized and legacy scalar-ID relation projections", () => {
    expect(isScalarIdRelation(resourceField({
      name: "owner",
      kind: "relation",
      relationModelLabel: "accounts.User",
      relationObject: false,
    }))).toBe(true);
    expect(isScalarIdRelation(resourceField({
      name: "owner",
      kind: "scalar",
      scalar: "ID",
      relationModelLabel: "accounts.User",
    }))).toBe(true);
    expect(isScalarIdRelation(resourceField({
      name: "owner",
      kind: "relation",
      relationModelLabel: "accounts.User",
      relationObject: true,
    }))).toBe(false);
  });

  test("answers field update capability from one metadata owner", () => {
    const metadata = modelMetadata({
      resource: { roots: { update: "updateLead" }, updateFields: ["stage"] },
      fields: {
        stage: resourceField({ name: "stage", kind: "relation", updatable: true }),
        name: resourceField({ name: "name", kind: "scalar", updatable: true }),
        code: resourceField({ name: "code", kind: "scalar", updatable: false }),
      },
    });

    expect(fieldUpdatable(metadata, "stage")).toBe(true);
    expect(fieldUpdatable(metadata, "name")).toBe(false);
    expect(fieldUpdatable(metadata, "code")).toBe(false);
    expect(fieldUpdatable(modelMetadata({
      resource: { roots: { update: "updateLead" }, updateFields: ["name"] },
      fields: { name: resourceField({ name: "name", kind: "scalar", updatable: true }) },
    }), "name")).toBe(true);
    expect(fieldUpdatable(modelMetadata({
      resource: { roots: { update: "updateLead" }, updateFields: ["name"] },
      fields: { name: resourceField({ name: "name", kind: "scalar", updatable: true }) },
    }), "name")).toBe(true);
    expect(fieldUpdatable(modelMetadata({
      resource: { roots: { update: "updateLead" } },
      fields: {},
    }), "declaredOnly")).toBe(true);
    expect(fieldUpdatable(modelMetadata({
      resource: { roots: { update: null }, updateFields: ["name"] },
      fields: { name: resourceField({ name: "name", kind: "scalar", updatable: true }) },
    }), "name")).toBe(false);
  });
});

function modelMetadata({
  resource,
  fields,
}: {
  resource?: Parameters<typeof testDataResource>[1];
  fields: Record<string, DataResourceFieldMetadata>;
}): ModelMetadata {
  const value = testDataResource("crm.Lead", {
    roots: {},
    ...resource,
    fields: Object.values(fields),
  });
  return schemaFieldMetadataFromDataResources([value]).labels["crm.Lead"]!;
}

function resourceField(
  overrides: Pick<DataResourceFieldMetadata, "name" | "kind">
    & Partial<DataResourceFieldMetadata>,
): DataResourceFieldMetadata {
  return {
    readable: true,
    filterable: false,
    sortable: false,
    aggregatable: false,
    groupable: false,
    creatable: false,
    updatable: false,
    requiredOnCreate: false,
    ...overrides,
  };
}


test("server order fields map display paths only to declared wire keys", () => {
  const resource = testDataResource("messaging.Message", { orderFields: ["sent_at", "thread__title__text", "oauth_client__is_enabled"] });
  expect(resourceOrderFieldForPath("sent_at", resource)).toBe("sent_at");
  expect(resourceOrderFieldForPath("sentAt", resource)).toBe("sent_at");
  expect(resourceOrderFieldForPath("thread.title.text", resource)).toBe("thread__title__text");
  expect(resourceOrderFieldForPath("oauthClient_IsEnabled", resource)).toBe("oauth_client__is_enabled");
  expect(resourceOrderFieldForPath("sender.party.display_name", resource)).toBeNull();
  expect(resourceOrderFieldForPath("thread.title.unknown", resource)).toBeNull();
  expect(resourceOrderFieldForPath("title", { ...resource, rowModel: "client" })).toBe("title");
  expect(resourceOrderFieldForPath("title", undefined)).toBe("title");
});
