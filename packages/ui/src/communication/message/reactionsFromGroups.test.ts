import { describe, expect, test, vi } from "vitest";

import { reactionsFromGroups } from "./index";

describe("reactionsFromGroups", () => {
  test("projects active state and routes count-only titles through i18n", () => {
    const count = vi.fn(
      (values: { reaction: string; count: string }) =>
        `reaction.count:${values.reaction}:${values.count}`,
    );

    expect(
      reactionsFromGroups(
        [{ reaction: "👍", count: 2, self_reacted: true, handles: [] }],
        { count, named: vi.fn() },
      ),
    ).toEqual([
      {
        reaction: "👍",
        count: 2,
        active: true,
        title: "reaction.count:👍:2",
      },
    ]);
    expect(count).toHaveBeenCalledWith({
      reaction: "👍",
      count: "2",
    });
  });

  test("prefers display names, falls back to handle values, and drops blanks", () => {
    const named = vi.fn(
      (values: { reaction: string; names: string }) =>
        `reaction.named:${values.names}`,
    );

    expect(
      reactionsFromGroups(
        [
          {
            reaction: "🎉",
            count: 3,
            handles: [
              { display_name: "Alex", value: "alex@example.test" },
              { display_name: "", value: "Sam" },
              { display_name: "  ", value: "" },
            ],
          },
        ],
        { count: vi.fn(), named },
      )[0]?.title,
    ).toBe("reaction.named:Alex, Sam");
  });
});
