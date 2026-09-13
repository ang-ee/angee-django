// @vitest-environment happy-dom

import { act, cleanup, render } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  resourceListProps: [] as Record<string, unknown>[],
  listProps: [] as Record<string, unknown>[],
  form: null as React.ReactNode,
}));

vi.mock("@angee/ui", () => ({
  Action: () => null,
  Column: () => null,
  Facet: () => null,
  Field: () => null,
  Form: () => null,
  Group: () => null,
  ListView: () => null,
  List: (props: Record<string, unknown> & { children?: React.ReactNode }) => {
    mocks.listProps.push(props);
    return <>{props.children}</>;
  },
  ResourceList: (props: Record<string, unknown> & { children?: React.ReactNode }) => {
    mocks.resourceListProps.push(props);
    return <>{props.children}</>;
  },
  useEnumOptions: () => [],
  useRecordActionMutation: () => [vi.fn()],
  useRouteHref: () => () => "/projects/tasks/tsk_1",
}));

vi.mock("../i18n", () => ({ useProjectsT: () => (key: string) => key }));

vi.mock("../task-actions", () => ({
  useTaskRowActions: () => [],
  useTaskFormDeclaration: () => mocks.form,
}));

import { ProjectsPage } from "./ProjectsPage";

beforeEach(() => {
  mocks.resourceListProps = [];
  mocks.listProps = [];
  mocks.form = <span data-testid="task-form" />;
});
afterEach(cleanup);

describe("project record, Tasks tab", () => {
  test("can add a task to the project it is looking at", () => {
    render(<ProjectsPage />);

    const tabs = mocks.resourceListProps[0]?.recordTabs as
      | readonly { id: string; render: (context: { recordId: string }) => React.ReactNode }[]
      | undefined;
    const tasksTab = tabs?.find((tab) => tab.id === "tasks");
    expect(tasksTab).toBeTruthy();

    mocks.resourceListProps = [];
    mocks.listProps = [];
    render(<>{tasksTab?.render({ recordId: "prj_7" })}</>);

    // Without a create surface of its own the tab could only list: adding a task
    // meant leaving for the global Tasks page and picking this project back out
    // of a relation picker.
    const tab = mocks.resourceListProps[0];
    expect(tab).toBeTruthy();
    expect(tab?.resource).toBe("projects.Task");
    expect(tab?.placement).toBe("drawer");
    expect(tab?.onSelect).toBeTypeOf("function");

    // The project is already known here, so it is a default rather than a field
    // to fill in — the whole point of creating from inside the project.
    expect(tab?.createDefaults).toEqual({ project: "prj_7" });

    // A create surface needs the task form declaration to render into.
    expect(
      React.Children.toArray(tab?.children as React.ReactNode).some(
        (child) =>
          React.isValidElement(child) &&
          (child.props as { "data-testid"?: string })["data-testid"] === "task-form",
      ),
    ).toBe(true);

    // The list it wraps still scopes to this project and deep-links its rows.
    const list = mocks.listProps[0];
    expect(list?.baseFilter).toEqual({ project: { exact: "prj_7" } });
  });

  test("opens and closes the create surface as the list asks", () => {
    render(<ProjectsPage />);
    const tabs = mocks.resourceListProps[0]?.recordTabs as
      | readonly { id: string; render: (context: { recordId: string }) => React.ReactNode }[]
      | undefined;
    const tasksTab = tabs?.find((tab) => tab.id === "tasks");

    mocks.resourceListProps = [];
    render(<>{tasksTab?.render({ recordId: "prj_7" })}</>);
    expect(mocks.resourceListProps.at(-1)?.creating).toBe(false);

    // `null` is how the list's own New control asks for a create.
    const onSelect = mocks.resourceListProps.at(-1)?.onSelect as (id: string | null) => void;
    act(() => onSelect(null));
    expect(mocks.resourceListProps.at(-1)?.creating).toBe(true);

    // Selecting a real row is not a create; rows deep-link through `rowHref`.
    act(() => onSelect("tsk_1"));
    expect(mocks.resourceListProps.at(-1)?.creating).toBe(false);

    act(() => onSelect(null));
    expect(mocks.resourceListProps.at(-1)?.creating).toBe(true);
    act(() => (mocks.resourceListProps.at(-1)?.onClose as () => void)());
    expect(mocks.resourceListProps.at(-1)?.creating).toBe(false);
  });
});
