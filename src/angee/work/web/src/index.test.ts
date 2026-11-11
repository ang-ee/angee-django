import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import work from "./index";

describe("work addon manifest", () => {
  test("satisfies rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(work)).not.toThrow();
  });

  test("makes each queue page its hub's record page, under the hub's path and anchor, so it renders in Work", () => {
    const routes = new Map((work.routes ?? []).map((route) => [route.name, route]));
    for (const [name, hub] of [
      ["work.board", "work.boards-hub"],
      ["work.triage", "work.triage-hub"],
      ["work.cycles", "work.cycles-hub"],
    ] as const) {
      expect(routes.get(hub)?.menu).toBe(hub);
      expect(routes.get(name)).toMatchObject({ parent: hub });
      expect(routes.get(name)?.menu).toBeUndefined();
      expect(routes.get(name)?.path.startsWith(`${routes.get(hub)?.path}/`)).toBe(true);
      // The hub's list is its index page, so the queue page replaces it.
      expect(routes.get(hub)?.component).toBeUndefined();
      expect(routes.get(hub)?.indexComponent).toBeDefined();
    }
    // A queue's cycles list is the cycle board's collection, which replaces it too.
    expect(routes.get("work.cycle-board")).toMatchObject({ parent: "work.cycles" });
    expect(routes.get("work.cycles")?.component).toBeUndefined();
    expect(routes.get("work.cycles")?.indexComponent).toBeDefined();
  });

  test("gates the task triage verbs on the task's server-projected admission", () => {
    // Start and Return read the task's server-projected admission, whichever form hosts them.
    expect(work.containers?.["projects.Task#actions-menu"]).toMatchObject({
      "work.task-triage-actions": { requiredFields: ["task_actions"] },
    });
  });
});
