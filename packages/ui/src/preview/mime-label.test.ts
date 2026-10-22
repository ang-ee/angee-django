import { describe, expect, test } from "vitest";

import { mimeKind } from "./mime-label";

describe("mimeKind", () => {
  test("reads exact types, families and parameters", () => {
    expect(mimeKind("application/pdf")).toBe("pdf");
    expect(mimeKind("image/png; charset=binary")).toBe("image");
    expect(mimeKind("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")).toBe("spreadsheet");
    expect(mimeKind("application/rss+xml")).toBe("xml");
    expect(mimeKind("text/x-python")).toBe("code");
    expect(mimeKind(null)).toBe("binary");
  });
});
