import {
  Action,
  Column,
  Facet,
  Field,
  Form,
  Group,
  List,
  ListView,
  type GanttViewSpec,
  useEnumOptions,
  useActionResultMutation,
  useRecordAction,
  defineRowAction,
  type StringIdRow,
  useResourceRecordHref,
  type RecordPanelContext,
  type RecordTabDescriptor,
} from "@angee/ui";
import * as React from "react";
import { useNavigate } from "@tanstack/react-router";

import { ProjectPhaseControl } from "./project-phase";
import { useProjectsT, type enProjectsMessages } from "./i18n";
import {
  MILESTONE_MODEL,
  PARTICIPANT_MODEL,
  PROJECT_MODEL,
  TASK_MODEL,
} from "./resources";
import { useTaskRowActions, type TaskActionRow } from "./task-actions";

/** Standard project form, shared by every route mounting projects. */
export function useProjectFormDeclaration(): React.ReactElement {
  const t = useProjectsT();
  const startResolutionOptions = useEnumOptions(PROJECT_MODEL, "start_date_resolution");
  const targetResolutionOptions = useEnumOptions(PROJECT_MODEL, "target_date_resolution");
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
      statusbar={({ recordId, form }) => recordId
        ? <ProjectPhaseControl recordId={recordId} readOnly={form.formReadOnly} />
        : null}
    >
      <Field name="title" title />
      <Field name="revision" readOnly hidden />
      <Field name="status" readOnly hidden />
      <Group label={t("project.group.planning")} columns={2}>
        <Field name="owner" readOnly />
        <Field name="lead" />
        <Field name="start_date" />
        <Field name="start_date_resolution" options={startResolutionOptions} />
        <Field name="target_date" />
        <Field name="target_date_resolution" options={targetResolutionOptions} />
      </Group>
      <Group label={t("project.group.details")} columns={2} collapsible defaultOpen={false}>
        <Field name="owns_items" />
        <Field name="folder" />
        <Field name="converted_from" readOnly />
      </Group>
      <Field name="body" widget="markdown.editor" body />
      <Action
        id="pause"
        label={t("project.action.pause")}
        icon="archive"
        run={pauseProject}
        visibleWhen={(record) => projectStatus(record) === "open"}
      />
      <Action
        id="resume"
        label={t("project.action.resume")}
        icon="activity"
        run={resumeProject}
        visibleWhen={(record) => ["paused", "dropped"].includes(projectStatus(record))}
      />
      <Action
        id="complete"
        placement="toolbar"
        label={t("project.action.complete")}
        icon="check"
        run={completeProject}
        visibleWhen={isActiveProject}
      />
      <Action
        id="drop"
        label={t("project.action.drop")}
        icon="circle-x"
        danger
        run={dropProject}
        visibleWhen={isActiveProject}
      />
    </Form>
  );
}

function isActiveProject(record: { status?: unknown }): boolean {
  const status = projectStatus(record);
  return status === "open" || status === "paused";
}

function projectStatus(record: { status?: unknown }): string {
  return String(record.status ?? "").trim().toLowerCase();
}

function ProjectTasksTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useProjectsT();
  const recordHref = useResourceRecordHref(TASK_MODEL);
  const navigate = useNavigate();
  const rowActions = useTaskRowActions<TaskActionRow>();
  return (
    <List<TaskActionRow>
      resource={TASK_MODEL}
      scope="local"
      baseFilter={{ project: { exact: recordId } }}
      order={{ sort_order: "ASC" }}
      rowActions={rowActions}
      onRowClick={recordHref ? (row) => {
        const to = recordHref(row.id);
        if (to) void navigate({ to });
      } : undefined}
      emptyContent={t("project.empty.tasks")}
    >
      <Column field="title" />
      <Column field="status" widget="statusBadge" />
      <Column field="assignee" />
      <Column field="priority" />
      <Column field="due_date" />
    </List>
  );
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

/** The project collection's columns, facets and initial group. */
export const projectListDeclaration = (
  <List resource={PROJECT_MODEL} defaultGroup={{ field: "status" }} order={{ updated_at: "DESC" }}>
    <Facet field="lead" />
    <Column field="title" />
    <Column field="current_milestone" />
    <Column field="status" widget="statusBadge" />
    <Column field="lead" />
    <Column field="target_date" />
    <Column field="updated_at" />
  </List>
);

function ProjectTabLabel({ message }: { message: keyof typeof enProjectsMessages }): React.ReactElement {
  const t = useProjectsT();
  return <>{t(message)}</>;
}

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
  id: "timeline", label: <ProjectTabLabel message="project.tabs.timeline" />,
  render: (context) => <ProjectTimelineTab {...context} />,
};

/** Standard saved-project panels, reusable without mounting the Projects route. */
export const projectRecordTabs: readonly RecordTabDescriptor[] = [
  projectTimelineTab,
  { id: "tasks", label: <ProjectTabLabel message="project.tabs.tasks" />, render: (context) => <ProjectTasksTab {...context} /> },
  { id: "milestones", label: <ProjectTabLabel message="project.tabs.milestones" />, render: (context) => <ProjectMilestonesTab {...context} /> },
  { id: "participants", label: <ProjectTabLabel message="project.tabs.participants" />, render: (context) => <ProjectParticipantsTab {...context} /> },
];
