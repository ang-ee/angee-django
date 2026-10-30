import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui";
import type { ChatterTabContent, ChatterViewContext, ContainerChild } from "@angee/ui/runtime";
import { describe, expect, test } from "vitest";

import projects, { PROJECT_MODEL, TASK_MODEL } from "./index";

const declaredMenus = composeAddons([{ id: projects.id, menus: projects.menus, routes: projects.routes }], { canonicalModelLabel: (model) => model }).menuComposition.logical;

describe("projects addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(projects)).not.toThrow();
  });

  test("declares collection owners, record children, and projection pages", () => {
    expect((projects.routes ?? []).map((route) => route.name)).toEqual([
      "projects.my-work",
      "projects.board",
      "projects.projects",
      "projects.projects.record",
      "projects.tasks",
      "projects.tasks.record",
    ]);
    expect(
      (projects.routes ?? []).find((route) => route.name === "projects.projects")
        ?.resource,
    ).toBe(PROJECT_MODEL);
    expect(
      (projects.routes ?? []).find((route) => route.name === "projects.tasks")
        ?.resource,
    ).toBe(TASK_MODEL);
    expect(
      (projects.routes ?? []).find((route) => route.name === "projects.board")
        ?.resource,
    ).toBeUndefined();
  });

  test("builds all hrefs from declared route templates", () => {
    const routeHref = createRouteHref(
      (projects.routes ?? []).map(({ name, path }) => ({ name, path })),
    );
    expect(routeHref("projects.my-work")).toBe("/projects/my-work");
    expect(routeHref("projects.projects.record", { id: "prj 1" })).toBe(
      "/projects/prj%201",
    );
    expect(routeHref("projects.tasks.record", { id: "task/1" })).toBe(
      "/projects/tasks/task%2F1",
    );
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

  test("owns one Projects place with four routed children", () => {
    expect(declaredMenus).toHaveLength(1);
    expect(declaredMenus?.[0]?.id).toBe("projects");
    expect(declaredMenus?.[0]?.route).toBeUndefined();
    expect(declaredMenus?.[0]?.children?.map((item) => item.route)).toEqual([
      "projects.my-work",
      "projects.projects",
      "projects.tasks",
      "projects.board",
    ]);
  });
});
