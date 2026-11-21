// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import {
  Column, Field, Form, Group, List, parsePageActions, parsePageColumns, parsePageFacets, parsePageFields, parsePageGroups,
  type FormProps, type ListProps, type ListViewProps, type RecordPanelContext, type ResourceListProps,
} from "@angee/ui";

import {
  MILESTONE_MODEL, PROJECT_MODEL, PROJECTS_OUTSIDE_TEMPLATES, TASK_MODEL, TASKS_OUTSIDE_TEMPLATES, projectListDeclaration, projectRecordTabs,
  projectGanttSpec, projectRecordTabsFor, projectTimelineSpec, projectTimelineTab, taskRecordTabs, taskRecordTabsFor, useProjectFormDeclaration, useProjectListDeclaration,
  useTaskFormDeclaration, useTaskListDeclaration,
} from "./index";
import { projectForm, taskForm } from "./record-forms";
import { ProjectsPage } from "./views/ProjectsPage";
import { TasksPage } from "./views/TasksPage";

// Keep the real declarations/parsers; only transport hooks and the page mount are replaced.
const transfer = vi.hoisted(() => vi.fn());
const mounted = vi.hoisted(() => ({ props: null as ResourceListProps | null, list: null as ListViewProps | null }));
vi.mock("@angee/ui", async () => {
  const actual = await vi.importActual<typeof import("@angee/ui")>("@angee/ui");
  return {
    ...actual,
    ResourceList: (props: ResourceListProps) => { mounted.props = props; return null; },
    ListView: (props: ListViewProps) => { mounted.list = props; return null; },
    useEnumOptions: () => [],
    useActionResultMutation: () => [vi.fn(), {}],
    useActionOutcomeMutation: (field: string) => [field === "transfer_project_ownership" ? transfer : vi.fn(), {}],
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
    // Templates stay out of the list and its Gantt lanes; the templates page lists them.
    expect(props.baseFilter).toEqual(PROJECTS_OUTSIDE_TEMPLATES);
    expect(projectGanttSpec.linked).toEqual({ resource: MILESTONE_MODEL, lane: "project" });
    const columns = parsePageColumns(props.children);
    expect(columns.map(({ field }) => field)).toEqual(["title", "current_milestone", "status", "lead", "target_date", "updated_at", "tags"]);
    expect(columns.find(({ field }) => field === "tags")?.hiddenByDefault).toBe(true);
    expect(parsePageFacets(props.children).map(({ field }) => field)).toEqual(["lead"]);
  });

  test("a caller's scope narrows the lists without bringing templates back", () => {
    const projects = propsOf<ListProps>(useProjectListDeclaration({ baseFilter: { lead: { exact: "usr_1" } } }));
    expect(projects.baseFilter).toEqual({ ...PROJECTS_OUTSIDE_TEMPLATES, lead: { exact: "usr_1" } });
    const { result: scoped } = renderHook(() => useTaskListDeclaration({ baseFilter: { assignee: { exact: "usr_1" } } }));
    expect(propsOf<ListProps>(scoped.current).baseFilter).toEqual({ ...TASKS_OUTSIDE_TEMPLATES, assignee: { exact: "usr_1" } });
    // A list scoped to one template's own tasks opts in.
    const { result: own } = renderHook(() => useTaskListDeclaration({
      baseFilter: { project: { exact: "prj_template" } }, includeTemplates: true,
    }));
    expect(propsOf<ListProps>(own.current).baseFilter).toEqual({ project: { exact: "prj_template" } });
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
    expect(parsePageGroups(props.children).find(({ pane }) => pane === "description")?.fields.map(({ name }) => name)).toEqual(["body"]);
    expect(fields.find(({ name }) => name === "status")).toMatchObject({ hidden: true, readOnly: true });
    expect(fields.find(({ name }) => name === "current_milestone")).toMatchObject({ status: true, widget: "projects.phase" });
    expect(fields.some(({ name }) => ["sort_order", "health"].includes(name))).toBe(false);
    const details = parsePageGroups(props.children).find(({ pane }) => pane === "project-settings");
    expect(details?.fields.map(({ name }) => name)).toEqual([
      "owner", "start_date_resolution", "target_date_resolution", "owns_items", "folder", "converted_from",
      "is_template", "created_from_template",
    ]);
    expect(parsePageActions(props.children).map(({ id }) => id))
      .toEqual(["transfer-ownership", "pause", "resume", "complete", "drop", "create-from-template"]);
    expect(props.returning).toContain("permissions");
    const actions = parsePageActions(props.children);
    expect(actions.find(({ id }) => id === "pause")?.visibleWhen?.({ status: "OPEN" })).toBe(true);
    expect(actions.find(({ id }) => id === "resume")?.visibleWhen?.({ status: "DROPPED" })).toBe(true);
    const fromTemplate = actions.find(({ id }) => id === "create-from-template");
    expect(fromTemplate?.visibleWhen?.({ is_template: true })).toBe(true);
    expect(fromTemplate?.visibleWhen?.({ is_template: false })).toBe(false);
  });

  test.each([
    ["OPEN", ["pause", "complete", "drop"], "complete"],
    ["PAUSED", ["resume", "complete", "drop"], "resume"],
    ["DROPPED", ["resume"], "resume"],
    ["DONE", [], undefined],
  ])("project %s keeps the next lifecycle verb primary", (status, expected, primary) => {
    const { result } = renderHook(useProjectFormDeclaration);
    const actions = parsePageActions(propsOf<FormProps>(result.current).children);
    const visible = actions.filter((action) => action.visibleWhen?.({ status, is_template: false }));
    expect(visible.map(({ id }) => id)).toEqual(expected);
    expect(visible.find((action) => action.primary)?.id).toBe(primary);
    expect(actions.find(({ id }) => id === "pause")?.placement).toBe("menu");
  });

  test.each(["OPEN", "PAUSED", "DROPPED", "DONE"])("a %s template offers only Create from template", (status) => {
    const { result } = renderHook(useProjectFormDeclaration);
    const actions = parsePageActions(propsOf<FormProps>(result.current).children);
    expect(actions.filter((action) => action.visibleWhen?.({ status, is_template: true })))
      .toEqual([expect.objectContaining({ id: "create-from-template", primary: true })]);
  });

  test("exports task list intent and keeps operational ordering out of the default form", () => {
    const { result: list } = renderHook(useTaskListDeclaration);
    const listProps = propsOf<ListProps>(list.current);
    expect(listProps.resource).toBe(TASK_MODEL);
    expect(listProps.defaultGroup).toEqual({ field: "project" });
    expect(listProps.baseFilter).toEqual(TASKS_OUTSIDE_TEMPLATES);
    expect(parsePageFacets(listProps.children).map(({ field }) => field)).toEqual(["project", "visibility", "assignee"]);
    expect(listProps.rowActions).toHaveLength(2);
    const { result: form } = renderHook(useTaskFormDeclaration);
    const groups = parsePageGroups(propsOf<FormProps>(form.current).children);
    expect(groups.filter(({ label }) => label !== "Details").flatMap(({ fields }) => fields)
      .some(({ name }) => ["sort_order", "sub_sort_order"].includes(name))).toBe(false);
    expect(groups.find(({ pane }) => pane === "task-details")?.fields.map(({ name }) => name))
      .toEqual(["delegate", "due_rule", "dropped_reason", "done_at", "dropped_at"]);
  });

  test("consumer selections keep dependency fields and reuse native groups and verbs", () => {
    const line = () => "Context";
    const { result: project } = renderHook(() => useProjectFormDeclaration({ groups: ["planning"], verbs: ["complete"], contextLine: line }));
    const projectProps = propsOf<FormProps>(project.current);
    expect(projectProps.contextLine).toBe(line);
    expect(parsePageGroups(projectProps.children).map(({ label }) => label)).toEqual(["Planning", "Description"]);
    expect(parsePageActions(projectProps.children).map(({ id }) => id)).toEqual(["complete"]);
    expect(parsePageActions(projectProps.children)[0]).toMatchObject({ label: "Complete", permission: "write", primary: true });
    expect(parsePageFields(projectProps.children).map(({ name }) => name)).toEqual([
      "title", "revision", "status", "current_milestone", "lead", "tags", "start_date", "target_date", "body",
    ]);

    const { result: task } = renderHook(() => useTaskFormDeclaration({ groups: ["assignment"], verbs: ["complete"], contextLine: line }));
    const taskProps = propsOf<FormProps>(task.current);
    expect(taskProps.contextLine).toBe(line);
    expect(parsePageGroups(taskProps.children).map(({ label }) => label)).toEqual(["Assignment", "Description"]);
    expect(parsePageActions(taskProps.children).map(({ id }) => id)).toEqual(["complete"]);
    expect(parsePageActions(taskProps.children)[0]).toMatchObject({ label: "Complete", permission: "write", primary: true });
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
    expect(actions.find(({ id }) => id === "complete")?.primary).toBe(true);
    expect(actions.find(({ id }) => id === "reopen")?.primary).toBe(true);
    expect(actions.find(({ id }) => id === "promote")?.placement).toBe("menu");
    expect(actions.find(({ id }) => id === "reset-due")?.placement).toBe("menu");
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
    expect(parsePageGroups(props.children).map(({ label }) => label)).toEqual(["Description", "Details"]);
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
    expect(mounted.props?.form).toBe(projectForm);
    const { result } = renderHook(() => (projectForm.Component as (props: { resource: string }) => React.ReactElement<FormProps>)({ resource: PROJECT_MODEL }) as React.ReactElement<FormProps>);
    expect(result.current.props.recordTabs).toBe(projectRecordTabs);
    expect(result.current.props.defaultRecordTab).toBe("description");
    expect(mountedDeclaration<ListProps>(List)).toBe(projectListDeclaration.props);
    const props = result.current.props;
    expect(parsePageFields(props.children).find(({ name }) => name === "current_milestone")?.status).toBe(true);
    expect(parsePageActions(props.children).map(({ id }) => id))
      .toEqual(["transfer-ownership", "pause", "resume", "complete", "drop", "create-from-template"]);
  });

  test("TasksPage mounts the exported task declarations and its default filter", () => {
    render(<TasksPage />);
    expect(mounted.props?.resource).toBe(TASK_MODEL);
    expect(mounted.props?.form).toBe(taskForm);
    const { result } = renderHook(() => (taskForm.Component as (props: { resource: string }) => React.ReactElement<FormProps>)({ resource: TASK_MODEL }) as React.ReactElement<FormProps>);
    expect(result.current.props.recordTabs).toBe(taskRecordTabs);
    expect(result.current.props.defaultRecordTab).toBe("description");
    expect(mounted.props?.defaultFilter).toEqual({ NOT: { status: { exact: "DROPPED" } } });
    expect(mountedDeclaration<ListProps>(List).defaultGroup).toEqual({ field: "project" });
    expect(parsePageFields(result.current.props.children).find(({ name }) => name === "title")?.title).toBe(true);
  });
});


test("Project transfer uses the actor-scoped user relation picker, permits clearing, and submits the record id", async () => {
  const { result } = renderHook(useProjectFormDeclaration);
  const props = propsOf<FormProps>(result.current);
  const action = parsePageActions(props.children).find(({ id }) => id === "transfer-ownership")!;
  expect(action).toMatchObject({ placement: "menu", permission: "transfer" });
  expect(action.args).toEqual([expect.objectContaining({ name: "owner", argKind: "relation", resource: "iam.User", optional: true })]);
  expect(props.returning).toContain("owner.id");
  const context = { record: { id: "prj_1", owner: { id: "usr_old" } }, selectedIds: ["prj_1"] };
  await action.submit?.({ owner: "usr_new" }, context);
  expect(transfer).toHaveBeenLastCalledWith("prj_1", { owner: "usr_new" });
  await action.submit?.({ owner: "" }, context);
  expect(transfer).toHaveBeenLastCalledWith("prj_1", { owner: null });
});
