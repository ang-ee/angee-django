import {
  Action,
  Badge,
  Column,
  Facet,
  Field,
  Form,
  Group,
  List,
  ListView,
  type GanttViewSpec,
  useEnumOptions,
  useStatusTone,
  optionLabel,
  useActionResultMutation,
  useRecordAction,
  defineRowAction,
  type StringIdRow,
  type WidgetOption,
  type RecordPanelContext,
  type RecordTabDescriptor,
  type FormProps,
} from "@angee/ui";
import * as React from "react";

import { enProjectsMessages, useProjectsT } from "./i18n";
import {
  MILESTONE_MODEL,
  PARTICIPANT_MODEL,
  PROJECT_MODEL,
  TASK_MODEL,
} from "./resources";
import { TaskManagementTab } from "./task-declarations";

export interface ProjectFormSelection {
  groups?: readonly ("planning" | "details")[];
  verbs?: readonly ("pause" | "resume" | "complete" | "drop")[];
  contextLine?: FormProps["contextLine"];
}

/** Standard project form, shared by every route mounting projects. */
export function useProjectFormDeclaration(selection: ProjectFormSelection = {}): React.ReactElement {
  const t = useProjectsT();
  const startResolutionOptions = useEnumOptions(PROJECT_MODEL, "start_date_resolution");
  const targetResolutionOptions = useEnumOptions(PROJECT_MODEL, "target_date_resolution");
  const statusOptions = useEnumOptions(PROJECT_MODEL, "status");
  const [pause] = useActionResultMutation("pause_project", {
    invalidateModels: [PROJECT_MODEL],
  });
  const [resume] = useActionResultMutation("resume_project", {
    invalidateModels: [PROJECT_MODEL],
  });
  const [complete] = useActionResultMutation("complete_project", {
    invalidateModels: [PROJECT_MODEL],
  });
  const [drop] = useActionResultMutation("drop_project", {
    invalidateModels: [PROJECT_MODEL],
  });
  const pauseProject = useRecordAction((id, context) => pause(id, { expected_revision: context.record?.revision }));
  const resumeProject = useRecordAction((id, context) => resume(id, { expected_revision: context.record?.revision }));
  const completeProject = useRecordAction((id, context) => complete(id, { expected_revision: context.record?.revision }));
  const dropProject = useRecordAction((id, context) => drop(id, { expected_revision: context.record?.revision }));
  return (
    <Form
      resource={PROJECT_MODEL}
      layout="tabs"
      returning={["permissions", "selectable_milestones.id", "current_milestone.name"]}
      headerExtras={({ record }) => record?.status
        ? <ProjectLifecycleBadge value={String(record.status)} options={statusOptions} />
        : null}
      contextLine={selection.contextLine}
    >
      <Field name="title" title />
      <Field name="revision" readOnly hidden />
      <Field name="status" readOnly hidden />
      <Field name="current_milestone" status widget="projects.phase" />
      {(selection.groups ?? ["planning", "details"]).includes("planning") ? <Group label={t("project.group.planning")} columns={2}>
        <Field name="owner" readOnly />
        <Field name="lead" />
        <Field name="start_date" />
        <Field name="start_date_resolution" options={startResolutionOptions} />
        <Field name="target_date" />
        <Field name="target_date_resolution" options={targetResolutionOptions} />
      </Group> : null}
      {(selection.groups ?? ["planning", "details"]).includes("details") ? <Group label={t("project.group.details")} columns={2} collapsible defaultOpen={false}>
        <Field name="owns_items" />
        <Field name="folder" />
        <Field name="converted_from" readOnly />
      </Group> : null}
      <Field name="body" widget="markdown.editor" body />
      {(selection.verbs ?? ["pause", "resume", "complete", "drop"]).includes("pause") ? <Action
        id="pause"
        label={t("project.action.pause")}
        icon="archive"
        run={pauseProject}
        visibleWhen={(record) => projectStatus(record) === "open"}
      /> : null}
      {(selection.verbs ?? ["pause", "resume", "complete", "drop"]).includes("resume") ? <Action
        id="resume"
        label={t("project.action.resume")}
        icon="activity"
        run={resumeProject}
        visibleWhen={(record) => ["paused", "dropped"].includes(projectStatus(record))}
      /> : null}
      {(selection.verbs ?? ["pause", "resume", "complete", "drop"]).includes("complete") ? <Action
        id="complete"
        placement="toolbar"
        label={t("project.action.complete")}
        icon="check"
        run={completeProject}
        visibleWhen={isActiveProject}
      /> : null}
      {(selection.verbs ?? ["pause", "resume", "complete", "drop"]).includes("drop") ? <Action
        id="drop"
        label={t("project.action.drop")}
        icon="circle-x"
        danger
        run={dropProject}
        visibleWhen={isActiveProject}
      /> : null}
    </Form>
  );
}

function ProjectLifecycleBadge({ value, options }: { value: string; options: readonly WidgetOption[] }): React.ReactElement {
  const tone = useStatusTone();
  return <Badge tone={tone(value)} density="compact" shape="pill">{optionLabel(options, value)}</Badge>;
}

function isActiveProject(record: { status?: unknown }): boolean {
  const status = projectStatus(record);
  return status === "open" || status === "paused";
}

function projectStatus(record: { status?: unknown }): string {
  return String(record.status ?? "").trim().toLowerCase();
}

interface MilestoneRow extends StringIdRow {
  reached_at?: unknown;
  revision?: unknown;
}

function ProjectMilestonesTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useProjectsT();
  const [markReached] = useActionResultMutation("mark_milestone_reached", {
    invalidateModels: [MILESTONE_MODEL],
  });
  const rowActions = React.useMemo(() => [defineRowAction<MilestoneRow>({
    kind: "page",
    id: "mark-reached",
    label: t("milestone.action.reach"),
    icon: "check",
    variant: "ghost",
    visible: (row) => !row.reached_at,
    pendingPolicy: "active-row",
    onSelect: (row) => markReached(row.id, { expected_revision: row.revision }),
  })], [markReached, t]);
  return (
    <ListView<MilestoneRow>
      resource={MILESTONE_MODEL}
      scope="local"
      fields={["revision"]}
      baseFilter={{ project: { exact: recordId } }}
      order={{ sort_order: "ASC" }}
      columns={[
        { field: "name" },
        { field: "start_date" },
        { field: "target_date" },
        { field: "reached_at" },
        { field: "reached_by" },
      ]}
      rowActions={rowActions}
      emptyContent={t("project.empty.milestones")}
    />
  );
}

function ProjectParticipantsTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useProjectsT();
  return (
    <ListView
      resource={PARTICIPANT_MODEL}
      scope="local"
      fields={["id", "party.display_name", "kind", "created_at"]}
      baseFilter={{ project: { exact: recordId } }}
      columns={[
        { field: "party.display_name" },
        { field: "kind" },
        { field: "created_at" },
      ]}
      emptyContent={t("project.empty.participants")}
    />
  );
}

/** Project rows own lanes and view state; milestone rows supply their bars. */
export const projectGanttSpec: GanttViewSpec = {
  linked: { resource: MILESTONE_MODEL, lane: "project" },
  start: "start_date", end: "target_date", label: "name", current: "current_milestone",
};

/** The project collection's columns, facets and initial group. */
export const projectListDeclaration = (
  <List resource={PROJECT_MODEL} defaultGroup={{ field: "status" }} order={{ updated_at: "DESC" }} gantt={projectGanttSpec}>
    <Facet field="lead" />
    <Column field="title" />
    <Column field="current_milestone" />
    <Column field="status" widget="statusBadge" />
    <Column field="lead" />
    <Column field="target_date" />
    <Column field="updated_at" />
  </List>
);

/** Milestone bars and task due-date markers on project lanes, including empty projects. */
export const projectTimelineSpec: GanttViewSpec = {
  start: "start_date",
  end: "target_date",
  label: "name",
  current: "current_milestone",
  markers: { resource: TASK_MODEL, lane: "project", date: "due_date", label: "title", tone: "status" },
};

function ProjectTimelineTab({ recordId }: RecordPanelContext): React.ReactElement {
  return <ListView
    resource={MILESTONE_MODEL}
    scope="local"
    presentation="embedded"
    defaultView="gantt"
    columns={[{ field: "name" }]}
    baseFilter={{ project: { exact: recordId } }}
    laneSource={{ field: "project", filters: [{ field: "id", operator: "eq", value: recordId }] }}
    gantt={projectTimelineSpec}
  />;
}

/** A record-tab-ready timeline; also part of the standard project tabs. */
export const projectTimelineTab: RecordTabDescriptor = {
  id: "timeline", label: { namespace: "projects", key: "project.tabs.timeline", fallback: enProjectsMessages["project.tabs.timeline"] },
  render: (context) => <ProjectTimelineTab {...context} />,
};

/** Standard saved-project panels, reusable without mounting the Projects route. */
export interface ProjectTabSelection {
  tabs?: readonly ("timeline" | "tasks" | "milestones" | "participants")[];
  /** Include consumer-owned descendant project tracks in the task scope. */
  taskProjectIds?: (context: RecordPanelContext) => readonly string[];
}

/** Select standard project tabs without copying their implementations. */
export function projectRecordTabsFor(selection: ProjectTabSelection = {}): readonly RecordTabDescriptor[] {
  const tabs: readonly RecordTabDescriptor[] = [
  projectTimelineTab,
  { id: "tasks", label: { namespace: "projects", key: "project.tabs.tasks", fallback: enProjectsMessages["project.tabs.tasks"] }, render: (context) => <TaskManagementTab {...context} relation="project" projectIds={selection.taskProjectIds?.(context)} /> },
  { id: "milestones", label: { namespace: "projects", key: "project.tabs.milestones", fallback: enProjectsMessages["project.tabs.milestones"] }, render: (context) => <ProjectMilestonesTab {...context} /> },
  { id: "participants", label: { namespace: "projects", key: "project.tabs.participants", fallback: enProjectsMessages["project.tabs.participants"] }, render: (context) => <ProjectParticipantsTab {...context} /> },
  ];
  return tabs.filter(({ id }) => !selection.tabs || selection.tabs.includes(id as "timeline" | "tasks" | "milestones" | "participants"));
}

export const projectRecordTabs = projectRecordTabsFor();
