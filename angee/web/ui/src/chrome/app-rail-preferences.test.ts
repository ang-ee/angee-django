import { describe, expect, test } from "vitest";

import {
  APP_RAIL_PREFERENCES_KEY,
  readAppRailPreferences,
  writeAppRailPreferences,
} from "./app-rail-preferences";

describe("app rail preferences", () => {
  test("reads a sanitized rail preference object", () => {
    expect(
      readAppRailPreferences({
        [APP_RAIL_PREFERENCES_KEY]: {
          order: ["ops", "notes", "ops", 4],
          defaultItemId: "ops",
          expanded: true,
        },
      }),
    ).toEqual({
      order: ["ops", "notes"],
      defaultItemId: "ops",
      expanded: true,
    });
  });

  test("falls back when the stored value is not a rail preference object", () => {
    expect(readAppRailPreferences({ [APP_RAIL_PREFERENCES_KEY]: "ops" }))
      .toEqual({
        order: [],
        defaultItemId: null,
        expanded: undefined,
      });
  });

  test("writes rail preferences without touching unrelated user preferences", () => {
    expect(
      writeAppRailPreferences(
        { theme: "dark" },
        {
          order: ["integrate", "notes"],
          defaultItemId: "integrate",
          expanded: false,
        },
      ),
    ).toEqual({
      theme: "dark",
      [APP_RAIL_PREFERENCES_KEY]: {
        order: ["integrate", "notes"],
        defaultItemId: "integrate",
        expanded: false,
      },
    });
  });
});
