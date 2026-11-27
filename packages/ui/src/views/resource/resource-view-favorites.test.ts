import { describe, expect, test } from "vitest";
import { favoriteFromResourceView } from "./model/favorites";
import { createResourceViewState } from "./resource-view-model";

import {
  RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY,
  RESOURCE_VIEW_FAVORITES_VERSION,
  readResourceViewFavoritesSlice,
} from "./resource-view-favorites";

const KEY = RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY;
const FAVORITE = { id: "favorite:open", label: "Open" };
const WHOLE_ORDER = { id: "favorite:ordered", label: "Ordered", sort: [{ field: "status", dir: "asc" }, { field: "rank", dir: "desc" }] };

describe("readResourceViewFavoritesSlice", () => {
  test("the document's version is 2, the one that holds a view's whole order", () => {
    expect(RESOURCE_VIEW_FAVORITES_VERSION).toBe(2);
    const slice = readResourceViewFavoritesSlice({ [KEY]: { version: 2, models: { "notes.Note": [FAVORITE, WHOLE_ORDER] } } });
    expect(slice).toEqual({ writable: true, document: { version: 2, models: { "notes.Note": [FAVORITE, WHOLE_ORDER] } } });
  });

  test("a version 1 document still reads, as version 2, so its next write records version 2", () => {
    const slice = readResourceViewFavoritesSlice({
      [KEY]: { version: 1, models: { "notes.Note": [{ ...FAVORITE, sort: { field: "title", dir: "asc" } }] } },
    });
    expect(slice).toEqual({ writable: true,
      document: { version: 2, models: { "notes.Note": [{ ...FAVORITE, sort: { field: "title", dir: "asc" } }] } } });
  });

  test("an unknown later version reads empty and write-unavailable", () => {
    const slice = readResourceViewFavoritesSlice({
      [KEY]: { version: 3, models: { "notes.Note": [FAVORITE] } },
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

  test("retains a pinned favourite in the existing preference version", () => {
    const slice = readResourceViewFavoritesSlice({
      [KEY]: { version: 1, models: { "notes.Note": [{ ...FAVORITE, pinned: true }] } },
    });
    expect(slice.document.models["notes.Note"]).toEqual([{ ...FAVORITE, pinned: true }]);
  });

  test("calendar favorites keep their shape without page, mode, or anchor; their hidden sources join it", () => {
    const favorite = favoriteFromResourceView(createResourceViewState({
      page: 3, pageSize: 20, view: "calendar", mode: "week", anchor: "2000-01-01",
    }), "Calendar");
    const document = { version: RESOURCE_VIEW_FAVORITES_VERSION, models: { "notes.Note": [favorite] } };
    const slice = readResourceViewFavoritesSlice({ [KEY]: JSON.parse(JSON.stringify(document)) });

    expect(slice.document).toEqual({
      version: RESOURCE_VIEW_FAVORITES_VERSION,
      models: { "notes.Note": [{ id: "favorite:calendar", label: "Calendar", pageSize: 20, view: "calendar", hiddenSources: [] }] },
    });
    expect(slice.writable).toBe(true);
  });
});
