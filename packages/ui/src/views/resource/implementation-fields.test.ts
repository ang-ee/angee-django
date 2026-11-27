import { expect, test } from "vitest";
import { schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";

import { isUnregisteredImplementationField } from "./implementation-fields";

const resource = testDataResource("storage.Backend", {
  implFields: ["backend_class"],
  fields: [{ name: "backend_class", kind: "enum", values: [{ value: "LOCAL" }], readable: true, aggregatable: false,
    creatable: true, updatable: true, requiredOnCreate: false }],
});
const model = schemaFieldMetadataFromDataResources([resource]).labels["storage.Backend"]!;

test("a stored null in an implementation column is marked, unless the record redacts the column", () => {
  expect(isUnregisteredImplementationField(model, { id: "b", backend_class: null }, "backend_class", null)).toBe(true);
  expect(isUnregisteredImplementationField(model, { id: "b", backend_class: null, redacted_fields: ["backend_class"] },
    "backend_class", null)).toBe(false);
  expect(isUnregisteredImplementationField(model, { id: "b", backend_class: null }, "backend_class", "LOCAL")).toBe(false);
  expect(isUnregisteredImplementationField(model, null, "backend_class", null)).toBe(false);
});
