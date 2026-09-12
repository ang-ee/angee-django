import * as React from "react";
import { expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui";
import { describe, expect, test } from "vitest";

import projects, { PROJECT_MODEL, TASK_MODEL } from "./index";

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

  test("creates a task through a one-column form, not the record's tabs", () => {
    // The record form is tabbed for the long tail; creating a task needs almost
    // none of it, and the tabs put five labels before the first field. The
    // override is the framework's own seam for that (`runtime.forms`).
    const form = projects.forms?.["projects.Task"];
    expect(form).toBeTruthy();

    const names = React.Children.toArray(
      (form as React.ReactElement<{ children?: React.ReactNode }>).props.children,
    ).flatMap((child) =>
      React.isValidElement(child)
        ? [(child.props as { name?: string }).name ?? ""]
        : [],
    );

    // Everything people set while creating, and nothing that belongs to the
    // record's long tail (ordering, recurrence, drop reason, release).
    expect(names).toEqual([
      "title",
      "note",
      "project",
      "queue",
      "stage",
      "assignee",
      "priority",
      "due_date",
      "estimate",
    ]);

    // Every name has to be creatable, or the dialog offers a field the mutation
    // will reject; these are the `project_tasks_insert_input` members.
    const creatable = new Set([
      "project", "milestone", "parent", "title", "note", "dropped_reason",
      "assignee", "delegate", "priority", "due_date", "recurrence", "sort_order",
      "sub_sort_order", "queue", "stage", "cycle", "estimate", "release",
    ]);
    for (const name of names) expect(creatable.has(name)).toBe(true);
  });

  test("owns one Projects place with four routed children", () => {
    expect(projects.menus).toHaveLength(1);
    expect(projects.menus?.[0]?.id).toBe("projects");
    // The header routes to the projects list. It used to carry no route and
    // fall through to its first child, so "Projects" the header went to My Work
    // while "Projects" the child went to the list: one name, two destinations.
    // This assertion was `toBeUndefined()` and pinned that behaviour.
    expect(projects.menus?.[0]?.route).toBe("projects.projects");
    expect(projects.menus?.[0]?.children?.map((item) => item.route)).toEqual([
      "projects.my-work",
      "projects.projects",
      "projects.tasks",
      "projects.board",
    ]);
    // My Work stays first, and the list child is named for what it is, so no two
    // entries in the group share a label.
    const labels = projects.menus?.[0]?.children?.map((item) => item.label) ?? [];
    expect(labels[0]).toBe("My Work");
    expect(labels).toContain("All projects");
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels).not.toContain(projects.menus?.[0]?.label);
  });
});
