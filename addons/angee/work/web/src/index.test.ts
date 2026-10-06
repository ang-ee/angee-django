import { expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui";
import { describe, expect, test } from "vitest";

import work, { QUEUE_MODEL } from "./index";

describe("work addon manifest", () => {
  test("satisfies rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(work)).not.toThrow();
  });

  test("declares queue ownership and resource-free task projections", () => {
    expect((work.routes ?? []).map((route) => route.name)).toEqual([
      "work.queues",
      "work.queues.record",
      "work.triage-hub",
      "work.triage",
      "work.boards-hub",
      "work.board",
      "work.cycles-hub",
      "work.cycles",
      "work.cycle-board",
    ]);
    expect(work.routes?.find((route) => route.name === "work.queues")?.resource).toBe(
      QUEUE_MODEL,
    );
    // work.cycles is a parameterized projection — it must NOT claim a
    // resource (a collection href cannot resolve at boot; the framework
    // now fails fast on it).
    expect(
      work.routes?.find((route) => route.name === "work.cycles")?.resource,
    ).toBeUndefined();
    expect(work.routes?.find((route) => route.name === "work.board")?.resource).toBeUndefined();
    expect(
      work.routes?.find((route) => route.name === "work.cycle-board")?.resource,
    ).toBeUndefined();
  });

  test("builds queue projection and cycle-board hrefs", () => {
    const href = createRouteHref(
      (work.routes ?? []).map(({ name, path }) => ({ name, path })),
    );
    expect(href("work.queues.record", { id: "queue 1" })).toBe(
      "/work/queues/queue%201",
    );
    expect(href("work.triage-hub")).toBe("/work/triage");
    expect(href("work.boards-hub")).toBe("/work/boards");
    expect(href("work.cycles-hub")).toBe("/work/cycles");
    expect(href("work.board", { queueId: "eng/1" })).toBe(
      "/work/boards/eng%2F1",
    );
    expect(href("work.triage", { queueId: "eng" })).toBe(
      "/work/triage/eng",
    );
    expect(href("work.cycles", { queueId: "eng" })).toBe(
      "/work/cycles/eng",
    );
    expect(href("work.cycle-board", { queueId: "eng", id: "cycle 3" })).toBe(
      "/work/cycles/eng/cycle%203",
    );
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

  test("registers one place, task extensions, and every work glyph", () => {
    expect(work.menus?.[0]?.children?.map((item) => item.route)).toEqual([
      "work.queues",
      "work.triage-hub",
      "work.boards-hub",
      "work.cycles-hub",
    ]);
    expect(containerChildren(work.containers).map(([id]) => id)).toEqual([
      "work.manager",
      "work.people-rail",
      "work.project-team",
      "work.task-fields",
      "work.queue-identity",
      "work.queue-triage",
      "work.queue-cadence",
      "work.queue-estimates",
      "work.queue-stages",
      "work.task-triage-actions",
    ]);
    // The queue settings sections are work.Queue's, so a product narrows them per route.
    expect(Object.keys(work.containers?.["work.Queue#sections"] ?? {})).toContain("work.queue-stages");
    // Menu verbs join the form's Actions menu; a bar in the toolbar would open a second one.
    expect(Object.keys(work.containers?.["projects.Task#actions-menu"] ?? {})).toEqual(["work.task-triage-actions"]);
    expect(work.containers?.["projects.Task#actions"]).toBeUndefined();
    expect(Object.keys(work.icons ?? {}).sort()).toEqual([
      "work",
      "work-accept",
      "work-board",
      "work-cycle",
      "work-cycle-close",
      "work-decline",
      "work-duplicate",
      "work-snooze",
      "work-start",
      "work-triage",
    ]);
  });
});

/** The manifest's container children, keyed by id, in declaration order. */
function containerChildren(containers: object | undefined): [string, { content?: unknown }][] {
  return Object.values(containers ?? {}).flatMap((entry) => (Array.isArray(entry) ? entry : [entry]) as Record<string, unknown>[])
    .flatMap((entry) => Object.entries(entry).filter(([id]) => id.includes(".")) as [string, { content?: unknown }][]);
}
