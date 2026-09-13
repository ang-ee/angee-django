// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  columns: [] as string[],
}));

vi.mock("@angee/ui", () => ({
  Column: ({ field }: { field: string }) => {
    mocks.columns.push(field);
    return null;
  },
  Facet: () => null,
  List: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  ResourceList: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  useRouteHref: () => () => "/projects/tasks/tsk_1",
}));
vi.mock("../i18n", () => ({ useProjectsT: () => (key: string) => key }));
vi.mock("../task-actions", () => ({
  useTaskRowActions: () => [],
  useTaskFormDeclaration: () => null,
}));

import { TasksPage } from "./TasksPage";

beforeEach(() => {
  mocks.columns = [];
});
afterEach(cleanup);

describe("tasks list columns", () => {
  test("offers stage and cycle so bulk edit can set them", () => {
    // Bulk edit builds its editable set from the list's columns. Without stage
    // and cycle here, the only way to plan an existing task into a cycle is to
    // create it on that cycle's board -- Linear's core planning move is missing.
    render(<TasksPage />);

    expect(mocks.columns).toContain("stage");
    expect(mocks.columns).toContain("cycle");
  });

  test("keeps the columns that were already there", () => {
    render(<TasksPage />);
    for (const field of [
      "title",
      "project.title",
      "status",
      "assignee",
      "priority",
      "due_date",
      "sort_order",
    ]) {
      expect(mocks.columns).toContain(field);
    }
  });
});
