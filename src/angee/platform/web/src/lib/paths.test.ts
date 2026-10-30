import { describe, expect, test } from "vitest";

import { platformScopeSearch } from "./paths";

describe("platformScopeSearch", () => {
  test("retains the platform model/addon search vocabulary", () => {
    expect(platformScopeSearch({ model: "notes.Note" })).toEqual({
      model: "notes.Note",
    });
    expect(platformScopeSearch({ addon: "example.notes" })).toEqual({
      addon: "example.notes",
    });
    expect(platformScopeSearch()).toBeUndefined();
  });
});
