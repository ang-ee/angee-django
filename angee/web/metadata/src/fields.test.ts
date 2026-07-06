import { describe, expect, test } from "vitest";

import {
  defaultWidgetForModelField,
  fieldUpdatable,
  filterFieldType,
} from "./fields";
import type { ModelFieldMetadata, ModelMetadata } from "./artifact";

describe("field metadata helpers", () => {
  test("treats Decimal as a number field with the numeric widget", () => {
    const field: ModelFieldMetadata = {
      name: "amount",
      kind: "scalar",
      scalar: "Decimal",
    };

    expect(defaultWidgetForModelField(field)).toBe("float");
    expect(filterFieldType("amount", field)).toBe("number");
  });

  test("resolves a money field to the money widget, filtering as a number", () => {
    const field: ModelFieldMetadata = {
      name: "amount",
      kind: "scalar",
      scalar: "Decimal",
      widget: "money",
      currencyField: "currency",
    };

    expect(defaultWidgetForModelField(field)).toBe("money");
    expect(filterFieldType("amount", field)).toBe("number");
  });

  test("answers field update capability from one metadata owner", () => {
    const metadata = modelMetadata({
      rootFields: { update: "updateLead", updateFields: ["stage"] },
      resource: { updateFields: ["name"] },
      fields: {
        stage: { name: "stage", kind: "relation", updatable: true },
        name: { name: "name", kind: "scalar", updatable: true },
        code: { name: "code", kind: "scalar", updatable: false },
      },
    });

    expect(fieldUpdatable(metadata, "stage")).toBe(true);
    expect(fieldUpdatable(metadata, "name")).toBe(false);
    expect(fieldUpdatable(metadata, "code")).toBe(false);
    expect(fieldUpdatable(modelMetadata({
      rootFields: { update: "updateLead" },
      resource: { updateFields: ["name"] },
      fields: { name: { name: "name", kind: "scalar", updatable: true } },
    }), "name")).toBe(true);
    expect(fieldUpdatable(modelMetadata({
      resource: { roots: { update: "updateLead" }, updateFields: ["name"] },
      fields: { name: { name: "name", kind: "scalar", updatable: true } },
    }), "name")).toBe(true);
    expect(fieldUpdatable(modelMetadata({
      rootFields: { update: "updateLead" },
      fields: {},
    }), "declaredOnly")).toBe(true);
    expect(fieldUpdatable(modelMetadata({
      rootFields: {},
      resource: { updateFields: ["name"] },
      fields: { name: { name: "name", kind: "scalar", updatable: true } },
    }), "name")).toBe(false);
  });
});

function modelMetadata({
  rootFields,
  resource,
  fields,
}: {
  rootFields?: ModelMetadata["rootFields"];
  resource?: Partial<NonNullable<ModelMetadata["resource"]>>;
  fields: Record<string, ModelFieldMetadata>;
}): ModelMetadata {
  return {
    typeName: "LeadType",
    fields,
    rootFields,
    resource: {
      schemaName: "console",
      modelLabel: "crm.Lead",
      appLabel: "crm",
      modelName: "lead",
      publicIdField: "sqid",
      roots: {},
      typeNames: {},
      capabilities: [],
      fields: [],
      filterFields: [],
      orderFields: [],
      aggregateFields: [],
      groupByFields: [],
      relationAxes: [],
      ...resource,
    },
  };
}
