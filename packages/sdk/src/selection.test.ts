import { describe, expect, test } from "vitest";

import { typeNameForModel } from "./selection";

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
});
