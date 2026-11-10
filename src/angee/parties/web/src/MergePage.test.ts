// @vitest-environment happy-dom

import { describe, expect, test } from "vitest";

import type { MergePartyRecord, MergePersonRecord } from "./documents";
import { fieldComparisons } from "./MergePage";

const COMMON = ["display_name", "notes", "first_met_note"];
const PERSON = [...COMMON, "name_prefix", "given_name", "additional_name", "family_name", "name_suffix", "nickname", "birthday"];

function party(id: string, values: Partial<MergePartyRecord> = {}): MergePartyRecord {
  return {
    id,
    display_name: `Party ${id}`,
    display_avatar: null,
    notes: null,
    first_met_note: null,
    merge_scalar_fields: COMMON,
    handles: [],
    circle_members: [],
    relationships: [],
    inbound_relationships: [],
    ...values,
  };
}

function person(id: string, values: Partial<MergePersonRecord> = {}): MergePersonRecord {
  return {
    id,
    merge_scalar_fields: PERSON,
    name_prefix: "",
    given_name: "",
    additional_name: "",
    family_name: "",
    name_suffix: "",
    nickname: null,
    birthday: null,
    ...values,
  };
}

describe("fieldComparisons", () => {
  test("compares the scalars both parties declare, read from the party records", () => {
    const comparisons = fieldComparisons(
      party("left", { display_name: "Ada", notes: "met at the lab" }),
      party("right", { display_name: "Ada L." }),
      null,
      null,
    );
    expect(comparisons).toEqual([
      { name: "display_name", left: "Ada", right: "Ada L." },
      { name: "notes", left: "met at the lab", right: null },
      { name: "first_met_note", left: null, right: null },
    ]);
  });

  test("adds the person scalars only when both sides are people", () => {
    const left = party("left");
    const right = party("right");
    const people = fieldComparisons(left, right, person("left", { given_name: "Ada" }), person("right", { birthday: "1815-12-10" }));
    expect(people.map((field) => field.name)).toEqual(PERSON);
    expect(people.find((field) => field.name === "given_name")).toEqual({ name: "given_name", left: "Ada", right: "" });
    expect(people.find((field) => field.name === "birthday")).toEqual({ name: "birthday", left: null, right: "1815-12-10" });
    expect(fieldComparisons(left, right, person("left"), null).map((field) => field.name)).toEqual(COMMON);
  });

  test("offers only the scalars both sides admit and the document selects", () => {
    const declared = [...COMMON, "tax_country"];
    const comparisons = fieldComparisons(
      party("left", { merge_scalar_fields: declared }),
      party("right", { merge_scalar_fields: ["display_name", "tax_country"] }),
      null,
      null,
    );
    expect(comparisons.map((field) => field.name)).toEqual(["display_name"]);
  });
});
