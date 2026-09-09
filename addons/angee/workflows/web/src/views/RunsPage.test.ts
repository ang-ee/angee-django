import { describe, expect, test } from "vitest";

import { attemptStateLabel, inspectionSelectionSearch, mapItemLabel, runCollectionFilter, runOriginLabel, waitLabel } from "./RunsPage";

const labels: Record<string, string> = {
  "runs.waitScheduled": "Scheduled",
  "runs.waitApproval": "Needs approval",
  "runs.waitExternal": "Waiting for input",
  "runs.waitChildren": "Waiting for steps",
  "runs.waitUnknown": "Waiting",
  "runs.originTest": "Test",
  "runs.originProduction": "Production",
  "runs.returnedUnapplied": "Returned · not applied",
  "runs.revokedAttempt": "Revoked",
  "runs.mapItem": "Item {index}",
};
const t = ((key: string, values?: Record<string, unknown>) => {
  const label = labels[key] ?? key;
  return values ? label.replace("{index}", String(values.index)) : label;
}) as never;

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

  test("identifies test runs without presenting version zero as a publication", () => {
    expect(runOriginLabel("TEST", t)).toBe("Test");
    expect(runOriginLabel("MANUAL", t)).toBe("Production");
  });

  test("labels only actual Map item indexes", () => {
    expect(mapItemLabel(-1, t)).toBe("—");
    expect(mapItemLabel(null, t)).toBe("—");
    expect(mapItemLabel(0, t)).toBe("Item 0");
    expect(mapItemLabel(2, t)).toBe("Item 2");
  });

  test("keeps execution and attempt selection in stable run URL state", () => {
    expect(inspectionSelectionSearch({ tab: "automations", page: 3, filters: ["mine"], execution: "old", attempt: "attempt-1" }, {
      execution: "execution-2",
      attempt: null,
    })).toEqual({ tab: "automations", page: 3, filters: ["mine"], execution: "execution-2" });
    expect(inspectionSelectionSearch({ tab: "automations", execution: "execution-2" }, {
      attempt: "attempt-2",
    })).toEqual({ tab: "automations", execution: "execution-2", attempt: "attempt-2" });
  });

  test("distinguishes returned, unapplied and revoked physical attempts from successful outcomes", () => {
    expect(attemptStateLabel({ id: "a", status: "completed", result_kind: "ERROR", applied_at: null }, t)).toBe("Returned · not applied");
    expect(attemptStateLabel({ id: "a", status: "completed", result_kind: "WAIT", applied_at: "now" }, t)).toBe("completed");
    expect(attemptStateLabel({ id: "a", status: "started", lease_revoked_at: "now" }, t)).toBe("Revoked");
  });
});
