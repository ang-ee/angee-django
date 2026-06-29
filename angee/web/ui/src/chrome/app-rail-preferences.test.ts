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
        },
      }),
    ).toEqual({
      order: ["ops", "notes"],
      defaultItemId: "ops",
    });
  });

  test("falls back when the stored value is not a rail preference object", () => {
    expect(readAppRailPreferences({ [APP_RAIL_PREFERENCES_KEY]: "ops" }))
      .toEqual({
        order: [],
        defaultItemId: null,
      });
  });

  test("writes rail preferences without touching unrelated user preferences", () => {
    expect(
      writeAppRailPreferences(
        { theme: "dark" },
        {
          order: ["integrate", "notes"],
          defaultItemId: "integrate",
        },
      ),
    ).toEqual({
      theme: "dark",
      [APP_RAIL_PREFERENCES_KEY]: {
        order: ["integrate", "notes"],
        defaultItemId: "integrate",
      },
    });
  });
});
