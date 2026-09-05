import { describe, expect, test } from "vitest";
import { Filter } from "./filter";

describe("Filter text control", () => {
  test("editing or clearing search preserves sibling lookup operators", () => {
    const original = {
      title: { startsWith: "A", iContains: "old", isNull: false },
      status: { exact: "active" },
    };

    const edited = Filter.from(original).withTextTerm("  new  ");
    expect(edited).toEqual({
      title: { startsWith: "A", iContains: "new", isNull: false },
      status: { exact: "active" },
    });
    expect(Filter.from(edited).withTextTerm("  ")).toEqual({
      title: { startsWith: "A", isNull: false },
      status: { exact: "active" },
    });
    expect(original.title.iContains).toBe("old");
  });

  test("clearing the only lookup removes only the text field", () => {
    expect(Filter.from({ name: { iContains: "text" }, active: true }).withTextTerm("", "name"))
      .toEqual({ active: true });
  });
});
