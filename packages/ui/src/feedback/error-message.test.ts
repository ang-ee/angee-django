import { describe, expect, test } from "vitest";

import { errorMessage } from "./error-message";

describe("errorMessage", () => {
  test("returns Error.message and falls back for non-Error values", () => {
    expect(errorMessage(new Error("boom"), "fallback")).toBe("boom");
    expect(errorMessage("boom", "fallback")).toBe("fallback");
  });
});
