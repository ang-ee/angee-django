import { describe, expect, test } from "vitest";

import {
  refineInvalidationParams,
  resourceInvalidationTargets,
} from "./invalidation";
import {
  schemaFieldMetadataFromDataResources,
  type DataResourceMetadata,
} from "./metadata";

describe("resource invalidation targets", () => {
  test("maps model labels to refine resource invalidation targets", () => {
    const [target] = resourceInvalidationTargets(
      schemaFieldMetadataFromDataResources([resource()]),
      ["notes.Note"],
    );

    expect(target).toEqual({
      resource: "notes",
      dataProviderName: "console",
    });
    expect(refineInvalidationParams(target!)).toEqual({
      resource: "notes",
      dataProviderName: "console",
      invalidates: ["list", "many", "detail"],
    });
  });

  test("fails fast when a mutation declares an unknown model invalidation target", () => {
    expect(() =>
      resourceInvalidationTargets(
        schemaFieldMetadataFromDataResources([resource()]),
        ["storage.File"],
      ),
    ).toThrow(
      'Action invalidation target "storage.File" is not exposed in resource metadata.',
    );
  });
});

function resource(): DataResourceMetadata {
  return {
    schemaName: "console",
    modelLabel: "notes.Note",
    appLabel: "notes",
    modelName: "Note",
    publicIdField: "id",
    roots: {
      list: "notes",
      detail: "notes_by_pk",
    },
    typeNames: {},
    capabilities: ["list", "detail"],
    filterFields: [],
    orderFields: [],
    aggregateFields: [],
    groupByFields: [],
    relationAxes: [],
  };
}
