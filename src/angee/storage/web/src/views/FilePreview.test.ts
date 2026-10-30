import { describe, expect, test } from "vitest";

import { FILE_PREVIEW_PAGE_PARAM, filePreviewPageFromSearch } from "./FilePreview";

describe("file preview page", () => {
  test("binds a one-based page to the exact File it was requested for", () => {
    const search = { [FILE_PREVIEW_PAGE_PARAM]: "fil_source:2" };
    expect(filePreviewPageFromSearch(search, "fil_source")).toBe(2);
    expect(filePreviewPageFromSearch(search, "fil_other")).toBeNull();
  });
});
