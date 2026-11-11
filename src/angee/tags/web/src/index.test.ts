import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import tags from "./index";

describe("angee.tags addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(tags)).not.toThrow();
  });

  test("registers the record-tags widget other forms resolve by key and the Tag form by model label", () => {
    expect(tags.widgets?.["angee.tags.recordTags"]).toBeDefined();
    expect(tags.forms?.["tags.Tag"]).toBeDefined();
  });
});
