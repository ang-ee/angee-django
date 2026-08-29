import { describe, expect, test } from "vitest";

import { publicIdLabel, rowPublicId, rowValueAtPath } from "./rows";

describe("rowValueAtPath", () => {
  test("reads nested projected values by dotted selection path", () => {
    const row = { markdown: { word_count: 321 } };

    expect(rowValueAtPath(row, "markdown.word_count")).toBe(321);
    expect(rowValueAtPath(row, "markdown.missing")).toBeUndefined();
    expect(rowValueAtPath(row, "markdown.word_count.value")).toBeUndefined();
  });
});

describe("rowPublicId", () => {
  test("returns a resource row's string id", () => {
    expect(rowPublicId({ id: "nte_123", title: "A" })).toBe("nte_123");
  });

  test("reads the resource-owned public id field when supplied", () => {
    expect(
      rowPublicId(
        { id: "db_1", public_id: "nte_123", title: "A" },
        { publicIdField: "public_id" },
      ),
    ).toBe("nte_123");
  });

  test("returns null when a row has no public string id", () => {
    expect(rowPublicId({ id: 1 })).toBeNull();
    expect(rowPublicId({})).toBeNull();
    expect(rowPublicId(null)).toBeNull();
  });
});

describe("publicIdLabel", () => {
  test("returns the raw public id", () => {
    expect(publicIdLabel("drv_123")).toBe("drv_123");
  });

  test("returns null for a blank id", () => {
    expect(publicIdLabel("plain-id")).toBe("plain-id");
    expect(publicIdLabel("  ")).toBeNull();
  });
});
