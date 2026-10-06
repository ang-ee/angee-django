// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import {
  Column, Field, Form, Group, List, parsePageActions, parsePageColumns, parsePageFacets, parsePageFields, parsePageGroups,
  type FormProps, type ListProps, type ListViewProps, type RecordPanelContext, type ResourceListProps,
} from "@angee/ui";

import {
  MILESTONE_MODEL, PROJECT_MODEL, TASK_MODEL, projectListDeclaration, projectRecordTabs,
  projectGanttSpec, projectRecordTabsFor, projectTimelineSpec, projectTimelineTab, taskRecordTabs, taskRecordTabsFor, useProjectFormDeclaration, useProjectListDeclaration,
  useTaskFormDeclaration, useTaskListDeclaration,
} from "./index";
import { ProjectsPage } from "./views/ProjectsPage";
import { TasksPage } from "./views/TasksPage";

// Keep the real declarations/parsers; only transport hooks and the page mount are replaced.
const mounted = vi.hoisted(() => ({ props: null as ResourceListProps | null, list: null as ListViewProps | null }));
vi.mock("@angee/ui", async () => {
  const actual = await vi.importActual<typeof import("@angee/ui")>("@angee/ui");
  return {
    ...actual,
    ResourceList: (props: ResourceListProps) => { mounted.props = props; return null; },
    ListView: (props: ListViewProps) => { mounted.list = props; return null; },
    useEnumOptions: () => [],
    useActionResultMutation: () => [vi.fn(), {}],
    useActionOutcomeMutation: () => [vi.fn(), {}],
    useRecordActionMutation: () => [vi.fn(), {}],
    useAuthoredResourceMutation: () => [vi.fn(), {}],
    useActionResultRun: () => vi.fn(),
    useRecordAction: (run: unknown) => run,
  };
});

afterEach(() => { cleanup(); mounted.props = null; mounted.list = null; });

function propsOf<T>(element: React.ReactNode): T {
  if (!React.isValidElement<T>(element)) throw new Error("Expected a declaration element");
  return element.props;
}

function mountedDeclaration<T>(type: typeof Form | typeof List): T {
  const element = React.Children.toArray(mounted.props?.children).find((child) => React.isValidElement(child) && child.type === type);
  return propsOf<T>(element);
}

describe("composable standard project and task declarations", () => {
  test("exports the project list's resource, columns, facets and group", () => {
    expect(projectListDeclaration.type).toBe(List);
    const props = propsOf<ListProps>(projectListDeclaration);
    expect(props.resource).toBe(PROJECT_MODEL);
    expect(props.defaultGroup).toEqual({ field: "status" });
    expect(props.defaultGroups?.gantt).toBeNull();
    expect(props.gantt).toBe(projectGanttSpec);
    expect(projectGanttSpec.linked).toEqual({ resource: MILESTONE_MODEL, lane: "project" });
    expect(parsePageColumns(props.children).map(({ field }) => field)).toEqual(["title", "current_milestone", "status", "lead", "target_date", "updated_at"]);
    expect(parsePageFacets(props.children).map(({ field }) => field)).toEqual(["lead"]);
  });

  test("project list options choose views without seeding a Gantt group", () => {
    const declaration = useProjectListDeclaration({ availableViews: ["list", "gantt"], defaultView: "gantt" });
    const props = propsOf<ListProps>(declaration);
    expect(props.availableViews).toEqual(["list", "gantt"]);
    expect(props.defaultView).toBe("gantt");
    expect(props.defaultGroups?.gantt).toBeNull();
  });

  test("the project form declares its phase status field, hero, and lifecycle actions", () => {
    const { result } = renderHook(useProjectFormDeclaration);
    expect(result.current.type).toBe(Form);
    const props = propsOf<FormProps>(result.current);
    const fields = [...parsePageFields(props.children), ...parsePageGroups(props.children).flatMap((group) => group.fields)];
    expect(fields.find(({ name }) => name === "title")?.title).toBe(true);
    expect(fields.find(({ name }) => name === "body")?.body).toBe(true);
    expect(fields.find(({ name }) => name === "status")).toMatchObject({ hidden: true, readOnly: true });
    expect(fields.find(({ name }) => name === "current_milestone")).toMatchObject({ status: true, widget: "projects.phase" });
    expect(fields.some(({ name }) => ["sort_order", "health"].includes(name))).toBe(false);
    const details = parsePageGroups(props.children).find(({ label }) => label === "Details");
    expect(details).toMatchObject({ collapsible: true, defaultOpen: false });
    expect(details?.fields.map(({ name }) => name)).toEqual(["owns_items", "folder", "converted_from"]);
    expect(parsePageActions(props.children).map(({ id }) => id)).toEqual(["pause", "resume", "complete", "drop"]);
    expect(props.returning).toContain("permissions");
    const actions = parsePageActions(props.children);
    expect(actions.find(({ id }) => id === "pause")?.visibleWhen?.({ status: "OPEN" })).toBe(true);
    expect(actions.find(({ id }) => id === "resume")?.visibleWhen?.({ status: "DROPPED" })).toBe(true);
  });

  test("exports task list intent and keeps operational ordering out of the default form", () => {
    const { result: list } = renderHook(useTaskListDeclaration);
    const listProps = propsOf<ListProps>(list.current);
    expect(listProps.resource).toBe(TASK_MODEL);
    expect(listProps.defaultGroup).toEqual({ field: "project" });
    expect(parsePageFacets(listProps.children).map(({ field }) => field)).toEqual(["project", "visibility", "assignee"]);
    expect(listProps.rowActions).toHaveLength(2);
    const { result: form } = renderHook(useTaskFormDeclaration);
    const groups = parsePageGroups(propsOf<FormProps>(form.current).children);
    expect(groups.filter(({ label }) => label !== "Details").flatMap(({ fields }) => fields)
      .some(({ name }) => ["sort_order", "sub_sort_order"].includes(name))).toBe(false);
    expect(groups.find(({ label }) => label === "Details")?.fields.slice(0, 2).map(({ name, createOnly }) => ({ name, createOnly })))
      .toEqual([{ name: "sort_order", createOnly: true }, { name: "sub_sort_order", createOnly: true }]);
    expect(groups.find(({ label }) => label === "Details")).toMatchObject({ collapsible: true, defaultOpen: false });
  });

  test("consumer selections keep dependency fields and reuse native groups and verbs", () => {
    const line = () => "Context";
    const { result: project } = renderHook(() => useProjectFormDeclaration({ groups: ["planning"], verbs: ["complete"], contextLine: line }));
    const projectProps = propsOf<FormProps>(project.current);
    expect(projectProps.contextLine).toBe(line);
    expect(parsePageGroups(projectProps.children).map(({ label }) => label)).toEqual(["Planning"]);
    expect(parsePageActions(projectProps.children).map(({ id }) => id)).toEqual(["complete"]);
    expect(parsePageActions(projectProps.children)[0]).toMatchObject({ label: "Complete", permission: "write" });
    expect(parsePageFields(projectProps.children).map(({ name }) => name)).toEqual([
      "title", "revision", "status", "current_milestone", "owner", "lead", "start_date",
      "start_date_resolution", "target_date", "target_date_resolution", "body",
    ]);

    const { result: task } = renderHook(() => useTaskFormDeclaration({ groups: ["assignment"], verbs: ["complete"], contextLine: line }));
    const taskProps = propsOf<FormProps>(task.current);
    expect(taskProps.contextLine).toBe(line);
    expect(parsePageGroups(taskProps.children).map(({ label }) => label)).toEqual(["Assignment"]);
    expect(parsePageActions(taskProps.children).map(({ id }) => id)).toEqual(["complete"]);
    expect(parsePageActions(taskProps.children)[0]).toMatchObject({ label: "Complete", permission: "write" });
    expect(parsePageFields(taskProps.children).map(({ name }) => name)).toContain("revision");
  });

  test("task lifecycle verbs show where they would act and the row's task_actions admit them", () => {
    const { result } = renderHook(() => useTaskFormDeclaration());
    const props = propsOf<FormProps>(result.current);
    expect(parsePageFields(props.children).find(({ name }) => name === "task_actions")).toMatchObject({ hidden: true, readOnly: true });
    const actions = parsePageActions(props.children);
    const visible = (id: string, record: Record<string, unknown>) =>
      actions.find((action) => action.id === id)?.visibleWhen?.({ id: "task-1", ...record });
    // A promoted task in a rule-owned stage: open, writable, but its owner admits no hand verb.
    expect(visible("drop", { status: "OPEN", task_actions: [] })).toBe(false);
    expect(visible("complete", { status: "OPEN", task_actions: [] })).toBe(false);
    expect(visible("reopen", { status: "DONE", task_actions: [] })).toBe(false);
    expect(visible("drop", { status: "OPEN", task_actions: ["drop"] })).toBe(true);
    expect(visible("complete", { status: "OPEN", task_actions: ["complete"] })).toBe(true);
    expect(visible("reopen", { status: "DROPPED", task_actions: ["reopen"] })).toBe(true);
    // Admission alone does not offer a verb that would not act.
    expect(visible("reopen", { status: "OPEN", task_actions: ["reopen"] })).toBe(false);
  });

  test("a route can select task columns and status while keeping the shared form", () => {
    const { result: list } = renderHook(() => useTaskListDeclaration({
      children: <><Column field="title" /><Column field="stage_name" /></>,
      rowActions: [],
    }));
    expect(parsePageColumns(propsOf<ListProps>(list.current).children).map(({ field }) => field))
      .toEqual(["title", "stage_name"]);
    expect(propsOf<ListProps>(list.current).rowActions).toEqual([]);

    const { result: form } = renderHook(() => useTaskFormDeclaration({
      groups: [], verbs: [], statusField: { name: "stage", widget: "work.stage" }, returning: ["created_at"],
      extraFields: <Group label="Details"><Field name="due_date" /></Group>,
    }));
    const props = propsOf<FormProps>(form.current);
    expect(props.returning).toEqual(["created_at"]);
    expect(parsePageFields(props.children).find(({ name }) => name === "stage")).toMatchObject({
      status: true, readOnly: true, widget: "work.stage",
    });
    expect(parsePageGroups(props.children).map(({ label }) => label)).toEqual(["Details"]);
  });

  test("exports the standard record tabs and a timeline scoped to one project's lane", () => {
    expect(projectRecordTabs.map(({ id }) => id)).toEqual(["timeline", "tasks", "milestones", "participants"]);
    expect(taskRecordTabs.map(({ id }) => id)).toEqual(["subtasks"]);
    expect(projectRecordTabs[0]).toBe(projectTimelineTab);
    expect(projectRecordTabsFor({ tabs: ["tasks"] }).map(({ id }) => id)).toEqual(["tasks"]);
    expect(taskRecordTabsFor([])).toEqual([]);
    // This panel needs only the saved id, as a native RecordPanelContext supplies.
    render(projectTimelineTab.render({ recordId: "project-a", active: true } as RecordPanelContext & { active: boolean }));
    expect(mounted.list).toMatchObject({
      resource: MILESTONE_MODEL, scope: "local", defaultView: "gantt",
      baseFilter: { project: { exact: "project-a" } },
      laneSource: { field: "project", filters: [{ field: "id", operator: "eq", value: "project-a" }] },
      gantt: projectTimelineSpec,
    });
    expect(projectTimelineSpec).toMatchObject({ current: "current_milestone", start: "start_date", end: "target_date",
      markers: { resource: TASK_MODEL, lane: "project", date: "due_date" },
    });
    const participants = projectRecordTabs.find(({ id }) => id === "participants")!;
    render(participants.render({ recordId: "project-a", active: true } as RecordPanelContext & { active: boolean }));
    expect(mounted.list).toMatchObject({
      resource: "projects.Participant", presentation: "embedded",
      baseFilter: { project: { exact: "project-a" } },
    });
  });

  test("ProjectsPage mounts the exported list, form and tabs", () => {
    render(<ProjectsPage />);
    expect(mounted.props?.resource).toBe(PROJECT_MODEL);
    expect(mounted.props?.recordTabs).toBe(projectRecordTabs);
    expect(mountedDeclaration<ListProps>(List)).toBe(projectListDeclaration.props);
    const props = mountedDeclaration<FormProps>(Form);
    expect(parsePageFields(props.children).find(({ name }) => name === "current_milestone")?.status).toBe(true);
    expect(parsePageActions(props.children).map(({ id }) => id)).toEqual(["pause", "resume", "complete", "drop"]);
  });

  test("TasksPage mounts the exported task declarations and its default filter", () => {
    render(<TasksPage />);
    expect(mounted.props?.resource).toBe(TASK_MODEL);
    expect(mounted.props?.recordTabs).toBe(taskRecordTabs);
    expect(mounted.props?.defaultFilter).toEqual({ NOT: { status: { exact: "DROPPED" } } });
    expect(mountedDeclaration<ListProps>(List).defaultGroup).toEqual({ field: "project" });
    expect(parsePageFields(mountedDeclaration<FormProps>(Form).children).find(({ name }) => name === "title")?.title).toBe(true);
  });
});
