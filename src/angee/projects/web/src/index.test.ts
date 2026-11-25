import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import type { ChatterTabContent, ChatterViewContext, ContainerChild } from "@angee/ui/runtime";
import { describe, expect, test } from "vitest";

import projects, { TASK_MODEL } from "./index";

describe("projects addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(projects)).not.toThrow();
  });

  test("guard: the phase widget it registers writes its own value, so a record form leaves it its own editability", () => {
    expect(projects.widgets?.["projects.phase"]).toHaveProperty("writesOwnValue", true);
  });

  test("offers the Tasks tab only on records whose type takes tasks as their subject", () => {
    const tasks = (projects.containers?.["record#aside"] as Record<string, ContainerChild<ChatterTabContent>>)["projects.tasks"]!;
    const record = (recordEdges?: readonly string[]): ChatterViewContext => ({
      pathname: "/records/rec_1", params: { id: "rec_1" },
      route: { name: "record", path: "/records/$id", viewType: "example/record", modelLabel: "example.Record", ...(recordEdges ? { recordEdges } : {}) },
      view: { kind: "record", type: "example/record", sqid: "rec_1" },
    });
    // The chatter shell offers a tab without a `when` on every record.
    const offered = (recordEdges?: readonly string[]) => tasks.content.when?.(record(recordEdges)) ?? true;
    // A record type takes tasks once its resource lists the task's subject edge; chatter alone does not.
    expect(offered([TASK_MODEL, "messaging.ThreadAttachment"])).toBe(true);
    expect(offered([TASK_MODEL])).toBe(true);
    expect(offered(["messaging.ThreadAttachment", "storage.FileAttachment"])).toBe(false);
    expect(offered()).toBe(false);
  });

  test("composes on a host without a calendar: it names only containers its own requirements declare", () => {
    // Projects requires timeline (its `timeline#kinds` owner) and no calendar; the
    // due-task overlay lives in the projects_calendar bridge.
    const timeline = { id: "timeline", containers: { "timeline#kinds": {} } };
    expect(() => composeAddons([timeline, projects], { canonicalModelLabel: (model) => model })).not.toThrow();
  });
});
