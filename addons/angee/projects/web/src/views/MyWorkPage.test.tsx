// @vitest-environment happy-dom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { PrimaryPaneTestHost, ShellPageTestProviders } from "@angee/app/testing";
import { parsePageColumns, type ListProps } from "@angee/ui";

import { TASK_MODEL } from "../resources";
import type { TaskActionRow } from "../task-actions";

const mocks = vi.hoisted(() => ({
  lists: [] as ListProps<TaskActionRow>[],
  user: { id: "usr_1" } as { id: string } | null,
  rowActions: [{ id: "complete" }],
}));

// The real primary-pane seam and shell test providers; only the collection renderer,
// the session and the record route are replaced.
vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    List: (props: ListProps<TaskActionRow>) => {
      mocks.lists.push(props);
      return <div data-testid="task-list" />;
    },
    useRuntimeAuth: () => ({ user: mocks.user }),
    useResourceRecordHref: () => (id: string) => `/projects/tasks/${id}`,
  };
});

// Messaging owns the agenda pane; the page only composes it.
vi.mock("@angee/messaging", () => ({
  ActivityAgendaPane: () => <section aria-label="Activities due" data-testid="agenda-pane" />,
}));

vi.mock("../task-actions", () => ({
  useTaskRowActions: () => mocks.rowActions,
}));

import { MyWorkPage } from "./MyWorkPage";

const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });

function harness(page = true) {
  return (
    <RouterContextProvider router={router}><ShellPageTestProviders>
      <div data-testid="page-content">{page ? <MyWorkPage /> : null}</div>
      <PrimaryPaneTestHost />
    </ShellPageTestProviders></RouterContextProvider>
  );
}

function lastList(): ListProps<TaskActionRow> {
  const props = mocks.lists.at(-1);
  if (!props) throw new Error("No task list rendered");
  return props;
}

beforeEach(() => {
  mocks.lists = [];
  mocks.user = { id: "usr_1" };
});

afterEach(cleanup);

describe("MyWorkPage", () => {
  test("renders one route-owned task list of the actor's open tasks", () => {
    render(harness());

    const content = screen.getByTestId("page-content");
    expect(within(content).getAllByTestId("task-list")).toHaveLength(1);
    expect(within(content).queryByRole("heading")).toBeNull();
    const list = lastList();
    expect(list.resource).toBe(TASK_MODEL);
    // No local scope and no embedded presentation: the page list owns the route
    // query, so favourites, shipped views and Share work as on any page list.
    expect(list.scope).toBeUndefined();
    expect(list.presentation).toBeUndefined();
    expect(list.selectable).toBeUndefined();
    expect(list.baseFilter).toEqual({ assignee: { exact: "usr_1" }, status: { exact: "OPEN" } });
    expect(list.order).toEqual({ due_date: "ASC", sort_order: "ASC" });
    expect(list.rowActions).toBe(mocks.rowActions);
    expect(list.rowHref?.({ id: "tsk_1" } as TaskActionRow)).toBe("/projects/tasks/tsk_1");
    expect(parsePageColumns(list.children).map(({ field }) => field))
      .toEqual(["title", "project.title", "priority", "due_date"]);
  });

  test("publishes messaging's agenda pane into the shell's primary (left) pane", () => {
    render(harness());

    const primary = screen.getByTestId("shell-primary");
    expect(within(primary).getByRole("region", { name: "Activities due" })).toBeTruthy();
    expect(within(screen.getByTestId("page-content")).queryByTestId("agenda-pane")).toBeNull();
  });

  test("clears the primary pane when the page leaves", () => {
    const { rerender } = render(harness());
    expect(within(screen.getByTestId("shell-primary")).getByTestId("agenda-pane")).toBeTruthy();

    rerender(harness(false));

    expect(within(screen.getByTestId("shell-primary")).queryByTestId("agenda-pane")).toBeNull();
  });

  test("an anonymous session lists no tasks", () => {
    mocks.user = null;
    render(harness());

    expect(lastList().baseFilter).toEqual({ id: { inList: [] } });
  });
});
