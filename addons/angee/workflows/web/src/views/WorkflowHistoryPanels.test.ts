import { describe, expect, test } from "vitest";

import { workflowRunLineageFilter } from "./WorkflowHistoryPanels";

describe("workflow history collection contracts", () => {
  test("selects runs pinned to either the head or any published version", () => {
    expect(workflowRunLineageFilter("wfl_head")).toEqual({
      OR: [
        { workflow: { exact: "wfl_head" } },
        { "workflow.published_from": { exact: "wfl_head" } },
      ],
    });
  });
});
