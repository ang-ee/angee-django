import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import posts from "./index";

describe("posts addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(posts)).not.toThrow();
  });

  test("registers feed collection and detail routes", () => {
    expect((posts.routes ?? []).map((route) => route.name)).toEqual([
      "posts.feeds",
      "posts.feeds.record",
    ]);
  });

  test("registers the Posts menu at the feeds route", () => {
    expect(posts.menus).toEqual([
      expect.objectContaining({
        id: "posts",
        label: "Posts",
        route: "posts.feeds",
      }),
    ]);
  });
});
