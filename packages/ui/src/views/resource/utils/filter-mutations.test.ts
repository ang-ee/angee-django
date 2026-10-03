import { expect, test } from "vitest";
import { ResourceQuery } from "@angee/metadata";
import type { ResourceToolbarFilterOption } from "../../../toolbars";
import { mergeFilterOptions } from "./filter-mutations";

test("facet and inferred choices merge by executable predicate, keeping authored labels", () => {
  const query = ResourceQuery.forRows({ fields: { state: { scalar: "String" } } });
  const facet: ResourceToolbarFilterOption = {
    id: 'state:"open"', label: "Open", filter: { state: { exact: "open" } },
  };
  expect(mergeFilterOptions([facet], [
    { id: "state:open", label: "OPEN", filter: { state: { exact: "open" } } },
    { id: "closed", label: "Closed", filter: { state: { exact: "closed" } } },
  ], query)).toEqual([facet, {
    id: "closed", label: "Closed", filter: { state: { exact: "closed" } },
  }]);
});

test("merging choices keeps blank, null and literal null predicates distinct", () => {
  const query = ResourceQuery.forRows({ fields: { state: { scalar: "String" } } });
  const choices: ResourceToolbarFilterOption[] = [
    { id: 'state:""', label: "Blank", filter: { state: { exact: "" } } },
    { id: "state:null", label: "Missing", filter: { state: { isNull: true } } },
    { id: 'state:"null"', label: "Literal null", filter: { state: { exact: "null" } } },
  ];
  expect(mergeFilterOptions(choices, [
    { id: "state:", label: "Blank again", filter: { state: { exact: "" } } },
    { id: "state:literal-null", label: "Literal null again", filter: { state: { exact: "null" } } },
  ], query)).toEqual(choices);
});
