import { describe, expect, test } from "vitest";

import { urlFromGoConfig } from "./router";

describe("TanStack router provider helpers", () => {
  test("serializes refine path requests with flat query values", () => {
    expect(
      urlFromGoConfig({
        type: "path",
        to: "/notes",
        query: { page: 2, empty: "", missing: null },
        hash: "top",
      }),
    ).toBe("/notes?page=2#top");
  });
});
