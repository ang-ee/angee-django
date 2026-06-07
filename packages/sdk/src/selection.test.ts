import { describe, expect, test } from "vitest";

import {
  buildSelection,
  clampPageSize,
  MAX_PAGE_SIZE,
  printSelection,
  pluralFieldName,
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
    expect(singularFieldName("OAuthProvider")).toBe("oauthProvider");
    expect(singularFieldName("URLMapping")).toBe("urlMapping");
  });

  test("pluralFieldName pluralises the singular field", () => {
    expect(pluralFieldName("Note")).toBe("notes");
    expect(pluralFieldName("Category")).toBe("categories");
    expect(pluralFieldName("Class")).toBe("classes");
    expect(pluralFieldName("Day")).toBe("days");
  });

  test("pluralFieldName doubles a trailing z only after a vowel", () => {
    expect(pluralFieldName("Quiz")).toBe("quizzes");
    expect(pluralFieldName("Buzz")).toBe("buzzes");
    expect(pluralFieldName("Waltz")).toBe("waltzes");
  });
});

describe("clampPageSize", () => {
  test("clamps to the maximum page size and floors at 1", () => {
    expect(clampPageSize(50)).toBe(50);
    expect(clampPageSize(9999)).toBe(MAX_PAGE_SIZE);
    expect(clampPageSize(0)).toBe(1);
    expect(clampPageSize(-5)).toBe(1);
    expect(clampPageSize(20.7)).toBe(20);
  });
});
