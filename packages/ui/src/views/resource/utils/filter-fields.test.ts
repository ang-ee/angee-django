import { expect, test } from "vitest";
import type { ResourceToolbarFilterField } from "../../../toolbars";
import { mergeFilterFields } from "./filter-mutations";

const inferred: readonly ResourceToolbarFilterField[] = [{
  id: "status", field: "status", label: "Status", type: "selection",
  operators: ["exact", "inList", "isNull", "isNotNull"],
}];

test("facet options preserve the query's executable operator menu", () => {
  const options = [{ value: "active", label: "Active" }];
  expect(mergeFilterFields([{ id: "status", label: "State", options }], inferred)).toEqual([
    { ...inferred[0], label: "State", options },
  ]);
});

test("custom field operators can narrow but cannot expand resource capabilities", () => {
  expect(mergeFilterFields([{ id: "status", label: "State", operators: ["exact", "contains"] }], inferred)[0]?.operators)
    .toEqual(["exact"]);
  expect(mergeFilterFields([{ id: "status", label: "State", operators: ["contains"] }], inferred)).toEqual([]);
});

test("an alternate control id retains the target field's capability bounds", () => {
  const fields = mergeFilterFields([{ id: "status-picker", field: "status", label: "State", operators: ["contains", "inList"] }], inferred);
  expect(fields[0]?.operators).toEqual(["inList"]);
});
