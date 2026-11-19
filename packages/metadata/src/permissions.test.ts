import { describe, expect, test } from "vitest";

import { schemaFieldMetadataFromDataResources } from "./artifact";
import { allowsRecordPermission } from "./permissions";
import { testDataResource } from "./testing";
import type { Row } from "./rows";

const plain = testDataResource("test.Plain");
const protectedResource = testDataResource("test.Protected", { fields: [{ name: "permissions", kind: "list", readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }] });

const records: readonly { record: Row | null | undefined; allowed: boolean }[] = [
  { record: undefined, allowed: false },
  { record: null, allowed: false },
  { record: { id: "one" }, allowed: false },
  { record: { permissions: undefined }, allowed: false },
  { record: { permissions: null }, allowed: false },
  { record: { permissions: "delete" }, allowed: false },
  { record: { permissions: [] }, allowed: false },
  { record: { permissions: ["write"] }, allowed: false },
  { record: { permissions: ["delete"] }, allowed: true },
  { record: { permissions: ["write", "delete"] }, allowed: true },
];

describe("allowsRecordPermission", () => {
  test.each(records)("models without a permissions field defer authorization to the server ($record)", ({ record }) => {
    expect(allowsRecordPermission(record, "delete", plain)).toBe(true);
  });

  test.each(records)("models declaring permissions require the requested permission ($record)", ({ record, allowed }) => {
    expect(allowsRecordPermission(record, "delete", protectedResource)).toBe(allowed);
    const model = schemaFieldMetadataFromDataResources([protectedResource]).labels[protectedResource.modelLabel]!;
    expect(allowsRecordPermission(record, "delete", model)).toBe(allowed);
  });

  test("delete does not grant write", () => {
    expect(allowsRecordPermission({ permissions: ["delete"] }, "write", protectedResource)).toBe(false);
  });
});
