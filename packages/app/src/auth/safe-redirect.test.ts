import { describe, expect, it } from "vitest";

import { safeRedirectPath } from "./safe-redirect";

/** The value `?next=<encoded>` yields, decoded as a page reads it. */
function next(encoded: string): string | null {
  return new URLSearchParams(`next=${encoded}`).get("next");
}

describe("safeRedirectPath", () => {
  it("accepts same-origin rooted paths with their query and fragment", () => {
    expect(safeRedirectPath("/notes")).toBe("/notes");
    expect(safeRedirectPath("/notes/abc?q=1#h")).toBe("/notes/abc?q=1#h");
    expect(safeRedirectPath(next("/projects%2F7%3Ftab%3Dtasks%23row-3"))).toBe("/projects/7?tab=tasks#row-3");
    expect(safeRedirectPath("/")).toBe("/");
    // A percent-encoded tab that is never decoded stays a path on this origin.
    expect(safeRedirectPath("/%09/x")).toBe("/%09/x");
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

  it.each(["/%09/x", "/%0a/x", "/%0d%5C x", "//x", "/%5Cx", "https://x", "javascript:alert(1)"])(
    "rejects ?next=%s, which the browser would resolve off this origin",
    (encoded) => {
      expect(safeRedirectPath(next(encoded))).toBeNull();
    },
  );
});
