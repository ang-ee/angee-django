import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ResourceQuery } from "@angee/metadata";
import type { ResourceToolbarFilterOption } from "../../../toolbars";
import { developmentMode } from "../../../lib/development-mode";
import { mergeFilterOptions } from "./filter-mutations";

vi.mock("../../../lib/development-mode", () => ({ developmentMode: vi.fn() }));
beforeEach(() => vi.mocked(developmentMode).mockReturnValue(true));
afterEach(() => vi.restoreAllMocks());

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

test("an invalid named filter throws with its id and parse reason in development", () => {
  const query = ResourceQuery.forRows({ fields: { state: { scalar: "String" } } });
  expect(() => mergeFilterOptions([
    { id: "setup", label: "Accepted", filter: { "stage.name": { exact: "Accepted" } } },
  ], [], query)).toThrow('Search filter option "setup": filter.stage.name: unknown or non-filterable field.');
});

test("production omits invalid filters, reports each id once, and still deduplicates executable predicates", () => {
  vi.mocked(developmentMode).mockReturnValue(false);
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  const query = ResourceQuery.forRows({ fields: { state: { scalar: "String" } } });
  const invalid = { id: "setup", label: "Accepted", filter: { "stage.name": { exact: "Accepted" } } };
  const open = { id: "open", label: "Open", filter: { state: { exact: "open" } } };
  const closed = { id: "closed", label: "Closed", filter: { state: { exact: "closed" } } };
  const reported = new Set<string>();
  for (let render = 0; render < 2; render++) {
    expect(mergeFilterOptions([invalid, open], [
      { ...invalid }, { ...open, id: "inferred-open" }, closed,
    ], query, reported)).toEqual([open, closed]);
  }
  expect(error).toHaveBeenCalledExactlyOnceWith('Search filter option "setup": filter.stage.name: unknown or non-filterable field.');
});

test.each([true, false])("unexpected query bugs still throw with developmentMode=%s", (development) => {
  vi.mocked(developmentMode).mockReturnValue(development);
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  const query = ResourceQuery.forRows({ fields: { state: { scalar: "String" } } });
  const bug = new TypeError("unexpected query failure");
  vi.spyOn(query, "toWhere").mockImplementation(() => { throw bug; });
  expect(() => mergeFilterOptions([{ id: "open", label: "Open", filter: { state: { exact: "open" } } }], [], query)).toThrow(bug);
  expect(error).not.toHaveBeenCalled();
});
