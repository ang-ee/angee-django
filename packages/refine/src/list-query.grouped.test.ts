import { print } from "graphql";
import { expect, test } from "vitest";
import { groupedListQueryMeta } from "./list-query";

test("grouped list pages share one document and retain independent pagination", () => {
  const meta = groupedListQueryMeta(
    { root: "workflow_runs", aggregateRoot: "workflow_runs_aggregate", filterType: "WorkflowRunBoolExp", orderType: "WorkflowRunOrderBy" },
    ["id", "subject_model", "subject_id"],
    [
      { where: { status: { _eq: "queued" } }, page: 2, pageSize: 5 },
      { where: { status: { _eq: "done" } }, page: 1, pageSize: 20 },
    ],
  );
  const text = print(meta.document);
  expect(text).toContain("group_0_rows: workflow_runs");
  expect(text).toContain("group_1_rows: workflow_runs");
  expect(text).toContain("group_0_aggregate: workflow_runs_aggregate");
  expect(text).toContain("group_1_aggregate: workflow_runs_aggregate");
  expect(meta.variables).toMatchObject({
    group_0_limit: 5,
    group_0_offset: 5,
    group_0_where: { status: { _eq: "queued" } },
    group_1_limit: 20,
    group_1_offset: 0,
    group_1_where: { status: { _eq: "done" } },
  });
  expect((text.match(/query GroupedResourceList/g) ?? [])).toHaveLength(1);
});
