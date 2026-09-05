import { describe, expect, test } from "vitest";
import { favoriteFromResourceView } from "./model/favorites";
import { createResourceViewState } from "./resource-view-model";

import {
  RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY,
  readResourceViewFavoritesSlice,
} from "./resource-view-favorites";

const KEY = RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY;
const FAVORITE = { id: "favorite:open", label: "Open" };

describe("readResourceViewFavoritesSlice", () => {
  test("an unknown version reads empty and write-unavailable", () => {
    const slice = readResourceViewFavoritesSlice({
      [KEY]: { version: 2, models: { "notes.Note": [FAVORITE] } },
    });
    expect(slice.writable).toBe(false);
    expect(slice.document.models).toEqual({});
  });

  test("a missing version reads empty but stays writable", () => {
    const slice = readResourceViewFavoritesSlice({
      [KEY]: { models: { "notes.Note": [FAVORITE] } },
    });
    expect(slice.writable).toBe(true);
    expect(slice.document.models).toEqual({});
  });

  test("a malformed favorite drops without wiping its siblings", () => {
    const slice = readResourceViewFavoritesSlice({
      [KEY]: {
        version: 1,
        models: {
          "notes.Note": [FAVORITE, { id: 42 }],
          "tags.Tag": "not-a-list",
        },
      },
    });
    expect(slice.writable).toBe(true);
    expect(slice.document.models).toEqual({ "notes.Note": [FAVORITE] });
  });

  test("a valid document reads writable with its models", () => {
    const slice = readResourceViewFavoritesSlice({
      [KEY]: { version: 1, models: { "notes.Note": [FAVORITE] } },
    });
    expect(slice.writable).toBe(true);
    expect(slice.document.models).toEqual({ "notes.Note": [FAVORITE] });
  });

  test("calendar favorites keep the v1 shape without page, mode, or anchor", () => {
    const favorite = favoriteFromResourceView(createResourceViewState({
      page: 3, pageSize: 20, view: "calendar", mode: "week", anchor: "2000-01-01",
    }), "Calendar");
    const document = { version: 1, models: { "notes.Note": [favorite] } };
    const slice = readResourceViewFavoritesSlice({ [KEY]: JSON.parse(JSON.stringify(document)) });

    expect(slice.document).toEqual({
      version: 1,
      models: { "notes.Note": [{ id: "favorite:calendar", label: "Calendar", pageSize: 20, view: "calendar" }] },
    });
    expect(slice.writable).toBe(true);
  });
});
