import { describe, expect, test } from "vitest";

import { defineAngeeSchemaMetadata } from "./artifact";
import { testDataResource } from "./testing";

describe("generated subtitle metadata", () => {
  test("accepts declared dotted selection paths", () => {
    const resource = testDataResource("knowledge.Page", {
      subtitle: {
        created: "created_at",
        updated: "updated_at",
        wordCount: "markdown.word_count",
      },
    });

    expect(defineAngeeSchemaMetadata({ angee: { resources: [resource] } }))
      .toEqual({ angee: { resources: [resource] } });
  });

  test("rejects malformed subtitle selection paths", () => {
    const resource = {
      ...testDataResource("knowledge.Page"),
      subtitle: { wordCount: "markdown..word_count" },
    };

    expect(() =>
      defineAngeeSchemaMetadata({ angee: { resources: [resource] } }),
    ).toThrow(
      "schema metadata.angee.resources[0].subtitle.wordCount must be a dotted selection path.",
    );
  });

  test("rejects non-string subtitle facts", () => {
    const resource = {
      ...testDataResource("knowledge.Page"),
      subtitle: { created: 1 },
    };

    expect(() =>
      defineAngeeSchemaMetadata({ angee: { resources: [resource] } }),
    ).toThrow("schema metadata.angee.resources[0].subtitle.created must be a string.");
  });

  test("rejects subtitle facts outside the renderer vocabulary", () => {
    const resource = {
      ...testDataResource("knowledge.Page"),
      subtitle: { summary: "markdown.excerpt" },
    };

    expect(() =>
      defineAngeeSchemaMetadata({ angee: { resources: [resource] } }),
    ).toThrow(
      "schema metadata.angee.resources[0].subtitle.summary is not a supported subtitle fact.",
    );
  });
});
