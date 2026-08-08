import { describe, expect, test } from "vitest";
import {
  crudFiltersFromFilterRecord,
  hasuraWhereFromCrudFilters,
} from "@angee/refine";
import type { ModelMetadata } from "@angee/metadata";

import {
  archiveFacetValue,
  combineWithArchiveDefault,
  withArchiveFacetValue,
} from "./archive-facet";
import { Filter } from "./resource-view-model";

const FIELD = "is_archived";

function archivableMetadata(): ModelMetadata {
  return {
    typeName: "NoteType",
    fields: {
      is_archived: {
        name: "is_archived",
        kind: "scalar",
        scalar: "Boolean",
        filterable: true,
        archivable: true,
      },
    },
  };
}

describe("archive facet scope over the view filter", () => {
  test("round-trips each scope through the view filter", () => {
    for (const value of ["active", "archived", "all"] as const) {
      expect(archiveFacetValue(withArchiveFacetValue({}, FIELD, value), FIELD)).toBe(
        value,
      );
    }
  });

  test("an unmentioned or explicitly-false field reads active", () => {
    expect(archiveFacetValue({}, FIELD)).toBe("active");
    expect(archiveFacetValue({ [FIELD]: { exact: false } }, FIELD)).toBe("active");
    expect(archiveFacetValue({ [FIELD]: false }, FIELD)).toBe("active");
  });

  test("the active scope clears the field back to the default", () => {
    const filter = withArchiveFacetValue({ [FIELD]: { exact: true } }, FIELD, "active");
    expect(filter).toEqual({});
  });

  test("other filter entries survive a scope change", () => {
    const filter = withArchiveFacetValue(
      { status: { exact: "open" } },
      FIELD,
      "archived",
    );
    expect(filter).toEqual({
      status: { exact: "open" },
      [FIELD]: { exact: true },
    });
  });
});

describe("combineWithArchiveDefault", () => {
  test("a non-archivable model merges without a default", () => {
    expect(combineWithArchiveDefault(undefined, {}, null)).toBeUndefined();
    expect(
      combineWithArchiveDefault({ status: { exact: "open" } }, {}, null),
    ).toEqual({ status: { exact: "open" } });
  });

  test("an archivable model scopes to unarchived by default", () => {
    expect(combineWithArchiveDefault(undefined, {}, archivableMetadata())).toEqual({
      [FIELD]: { exact: false },
    });
  });

  test("an explicit view-filter scope wins over the default", () => {
    expect(
      combineWithArchiveDefault(
        undefined,
        { [FIELD]: { exact: true } },
        archivableMetadata(),
      ),
    ).toEqual({ [FIELD]: { exact: true } });
    expect(
      combineWithArchiveDefault(
        undefined,
        { [FIELD]: { inList: [false, true] } },
        archivableMetadata(),
      ),
    ).toEqual({ [FIELD]: { inList: [false, true] } });
  });

  test("a base-filter mention wins over the default, even nested", () => {
    expect(
      combineWithArchiveDefault(
        { [FIELD]: { exact: true } },
        {},
        archivableMetadata(),
      ),
    ).toEqual({ [FIELD]: { exact: true } });
    const nested = { AND: [{ [FIELD]: { exact: true } }] };
    expect(combineWithArchiveDefault(nested, {}, archivableMetadata())).toEqual(
      nested,
    );
  });
});

describe("filter shapes reach the wire", () => {
  test("Filter.mentionsField sees top-level and control-nested fields", () => {
    expect(Filter.from({ [FIELD]: { exact: true } }).mentionsField(FIELD)).toBe(true);
    expect(
      Filter.from({ OR: [{ status: { exact: "x" } }, { [FIELD]: false }] })
        .mentionsField(FIELD),
    ).toBe(true);
    expect(Filter.from({ status: { exact: "x" } }).mentionsField(FIELD)).toBe(false);
  });

  test("the default and the all scope encode to Hasura boolean operators", () => {
    expect(
      hasuraWhereFromCrudFilters(
        crudFiltersFromFilterRecord({ [FIELD]: { exact: false } }),
      ),
    ).toEqual({ [FIELD]: { _eq: false } });
    expect(
      hasuraWhereFromCrudFilters(
        crudFiltersFromFilterRecord({ [FIELD]: { inList: [false, true] } }),
      ),
    ).toEqual({ [FIELD]: { _in: [false, true] } });
  });
});

describe("relation picker option filters", () => {
  test("an archivable target appends the unarchived default", async () => {
    const { relationOptionFilters } = await import("./relation-options");
    expect(relationOptionFilters(undefined, FIELD)).toEqual([
      { field: FIELD, operator: "eq", value: false },
    ]);
    expect(
      relationOptionFilters([{ field: "kind", operator: "eq", value: "app_keys" }], FIELD),
    ).toEqual([
      { field: "kind", operator: "eq", value: "app_keys" },
      { field: FIELD, operator: "eq", value: false },
    ]);
  });

  test("an explicit caller filter on the flag wins; non-archivable stays untouched", async () => {
    const { relationOptionFilters } = await import("./relation-options");
    const explicit = [{ field: FIELD, operator: "eq", value: true }] as const;
    expect(relationOptionFilters(explicit, FIELD)).toBe(explicit);
    expect(relationOptionFilters(undefined, null)).toBeUndefined();
  });
});
