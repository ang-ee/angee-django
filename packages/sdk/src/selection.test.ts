import { describe, expect, test } from "vitest";

import {
  buildSelection,
  encodeOffsetCursor,
  pageToConnectionArgs,
  printSelection,
  pluralFieldName,
  RELAY_MAX_PAGE_SIZE,
  singularFieldName,
  typeNameForModel,
} from "./selection";

describe("buildSelection", () => {
  test("injects id at the root selection", () => {
    expect(buildSelection([])).toEqual([{ name: "id" }]);
  });

  test("adds scalar leaf fields after id, in order", () => {
    expect(buildSelection(["title", "state"])).toEqual([
      { name: "id" },
      { name: "title" },
      { name: "state" },
    ]);
  });

  test("expands a dotted path into a nested selection with id at each level", () => {
    expect(buildSelection(["owner.firstName"])).toEqual([
      { name: "id" },
      { name: "owner", children: [{ name: "id" }, { name: "firstName" }] },
    ]);
  });

  test("dedupes repeated leaves and merges sibling sub-selections", () => {
    expect(
      buildSelection(["owner.firstName", "owner.lastName", "title", "title"]),
    ).toEqual([
      { name: "id" },
      {
        name: "owner",
        children: [{ name: "id" }, { name: "firstName" }, { name: "lastName" }],
      },
      { name: "title" },
    ]);
  });

  test("rejects an invalid field name instead of silently dropping it", () => {
    expect(() => buildSelection(["bad-name"])).toThrow(/bad-name/);
  });
});

describe("printSelection", () => {
  test("prints leaves space-joined and nested selections in braces", () => {
    expect(printSelection(buildSelection(["title", "owner.firstName"]))).toBe(
      "id title owner { id firstName }",
    );
  });
});

describe("model naming", () => {
  test("typeNameForModel takes the final dotted segment", () => {
    expect(typeNameForModel("notes.Note")).toBe("Note");
    expect(typeNameForModel("Note")).toBe("Note");
  });

  test("typeNameForModel preserves interior capitalization", () => {
    expect(typeNameForModel("auth.OAuthProvider")).toBe("OAuthProvider");
  });

  test("typeNameForModel rejects an empty or dangling label", () => {
    expect(() => typeNameForModel("")).toThrow();
    expect(() => typeNameForModel("notes.")).toThrow();
  });

  test("singularFieldName lower-camelises the type", () => {
    expect(singularFieldName("Note")).toBe("note");
    expect(singularFieldName("OAuthProvider")).toBe("oAuthProvider");
  });

  test("pluralFieldName pluralises the singular field", () => {
    expect(pluralFieldName("Note")).toBe("notes");
    expect(pluralFieldName("Category")).toBe("categories");
    expect(pluralFieldName("Class")).toBe("classes");
    expect(pluralFieldName("Day")).toBe("days");
  });
});

describe("pagination", () => {
  test("the first page has a null cursor", () => {
    expect(pageToConnectionArgs(1, 50)).toEqual({ first: 50, after: null });
  });

  test("later pages encode the previous page's last offset as the cursor", () => {
    expect(pageToConnectionArgs(2, 50)).toEqual({
      first: 50,
      after: encodeOffsetCursor(49),
    });
    expect(pageToConnectionArgs(3, 20)).toEqual({
      first: 20,
      after: encodeOffsetCursor(39),
    });
  });

  test("clamps page size to the relay maximum and floors the page at 1", () => {
    expect(pageToConnectionArgs(1, 9999).first).toBe(RELAY_MAX_PAGE_SIZE);
    expect(pageToConnectionArgs(0, 50)).toEqual({ first: 50, after: null });
  });

  test("encodeOffsetCursor round-trips through the relay cursor encoding", () => {
    expect(atob(encodeOffsetCursor(5))).toBe("arrayconnection:5");
  });
});
