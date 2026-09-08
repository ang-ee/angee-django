import { describe, expect, test } from "vitest";

import { runCollectionFilter, waitLabel } from "./RunsPage";

const labels: Record<string, string> = {
  "runs.waitScheduled": "Scheduled",
  "runs.waitApproval": "Needs approval",
  "runs.waitExternal": "Waiting for input",
  "runs.waitChildren": "Waiting for steps",
  "runs.waitUnknown": "Waiting",
};
const t = ((key: string) => labels[key] ?? key) as never;

describe("RunsPage presentation", () => {
  test("filters each collection through the declared workflow purpose relation", () => {
    expect(runCollectionFilter("automations")).toEqual({
      "workflow.purpose": { exact: "AUTOMATION" },
    });
    expect(runCollectionFilter("sessions")).toEqual({
      "workflow.purpose": { exact: "AGENT_SESSION" },
    });
  });

  test.each([
    ["scheduled", "Scheduled"],
    ["approval", "Needs approval"],
    ["external", "Waiting for input"],
    ["children", "Waiting for steps"],
    ["", "Waiting"],
    [null, "Waiting"],
  ])("labels a waiting run with kind %s", (kind, expected) => {
    expect(waitLabel(kind, "WAITING", t)).toBe(expected);
  });

  test("does not surface stale wait metadata after a run leaves WAITING", () => {
    expect(waitLabel("scheduled", "SUCCEEDED", t)).toBeNull();
    expect(waitLabel("external", "RUNNING", t)).toBeNull();
  });
});
