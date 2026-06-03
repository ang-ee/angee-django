import { describe, expect, it } from "vitest";

import { safeRedirectPath } from "./safe-redirect";

describe("safeRedirectPath", () => {
  it("accepts same-site relative paths", () => {
    expect(safeRedirectPath("/notes")).toBe("/notes");
    expect(safeRedirectPath("/notes/abc?q=1#h")).toBe("/notes/abc?q=1#h");
    expect(safeRedirectPath("/")).toBe("/");
  });

  it("rejects empties and non-rooted values", () => {
    expect(safeRedirectPath(null)).toBeNull();
    expect(safeRedirectPath(undefined)).toBeNull();
    expect(safeRedirectPath("")).toBeNull();
    expect(safeRedirectPath("notes")).toBeNull();
    expect(safeRedirectPath("https://evil.example/")).toBeNull();
  });

  it("rejects protocol-relative and backslash open-redirect escapes", () => {
    expect(safeRedirectPath("//evil.example")).toBeNull();
    expect(safeRedirectPath("/\\evil.example")).toBeNull();
  });
});
