import { describe, expect, test } from "vitest";

import { allowsRecordPermission } from "./permissions";
import type { Row } from "./rows";

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
    expect(allowsRecordPermission(record, "delete", false)).toBe(true);
  });

  test.each(records)("models declaring permissions require the requested permission ($record)", ({ record, allowed }) => {
    expect(allowsRecordPermission(record, "delete", true)).toBe(allowed);
  });

  test("delete does not grant write", () => {
    expect(allowsRecordPermission({ permissions: ["delete"] }, "write", true)).toBe(false);
  });
});
