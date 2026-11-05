import { print } from "graphql";
import { expect, test } from "vitest";
import { groupedListQueryMeta } from "./list-query";

test("grouped list pages share one document and retain independent pagination", () => {
  const meta = groupedListQueryMeta(
    { root: "workflow_runs", aggregateRoot: "workflow_runs_aggregate", groupedRowsRoot: "workflow_runs_grouped_rows", groupByType: "WorkflowRunGroupBySpec", groupedRowPageType: "WorkflowRunGroupedRowPage", filterType: "WorkflowRunBoolExp", orderType: "WorkflowRunOrderBy" },
    ["id", "subject_model", "subject_id"],
    { field: "STATUS" },
    [
      { value: "queued", page: 2, pageSize: 5 },
      { value: "done", page: 1, pageSize: 20 },
    ],
    { active: { _eq: true } },
  );
  const text = print(meta.document);
  expect(text).toContain("rows: workflow_runs_grouped_rows");
  expect(text).not.toContain("workflow_runs_aggregate");
  expect(meta.variables).toMatchObject({
    group_by: { field: "STATUS" },
    pages: [{ key: "queued", limit: 5, offset: 5 }, { key: "done", limit: 20, offset: 0 }],
    where: { active: { _eq: true } },
  });
  expect((text.match(/query GroupedResourceList/g) ?? [])).toHaveLength(1);
});
