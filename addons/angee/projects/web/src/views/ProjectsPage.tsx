import {
  Action,
  Column,
  Facet,
  Field,
  Form,
  Group,
  List,
  ListView,
  ResourceList,
  useEnumOptions,
  useActionResultMutation,
  useRecordAction,
  defineRowAction,
  type StringIdRow,
  useRouteHref,
  type RecordPanelContext,
  type RecordTabDescriptor,
} from "@angee/ui";
import * as React from "react";

import { ProjectPhaseControl } from "../project-phase";
import { useProjectsT } from "../i18n";
import {
  MILESTONE_MODEL,
  PARTICIPANT_MODEL,
  PROJECT_MODEL,
  TASK_MODEL,
} from "../resources";
import { useTaskRowActions, type TaskActionRow } from "../task-actions";

/** Projects collection plus its one routed FormView record surface. */
export function ProjectsPage(): React.ReactElement {
  const t = useProjectsT();
  const statusOptions = useEnumOptions(PROJECT_MODEL, "status");
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
  const recordTabs = React.useMemo<readonly RecordTabDescriptor[]>(
    () => [
      {
        id: "tasks",
        label: t("project.tabs.tasks"),
        render: (context) => <ProjectTasksTab {...context} />,
      },
      {
        id: "milestones",
        label: t("project.tabs.milestones"),
        render: (context) => <ProjectMilestonesTab {...context} />,
      },
      {
        id: "participants",
        label: t("project.tabs.participants"),
        render: (context) => <ProjectParticipantsTab {...context} />,
      },
    ],
    [t],
  );

  return (
    <ResourceList resource={PROJECT_MODEL} placement="inline" routed recordTabs={recordTabs}>
      <List
        resource={PROJECT_MODEL}
        defaultGroup={{ field: "status" }}
        order={{ updated_at: "DESC" }}
      >
        <Facet field="lead" label={t("common.lead")} />
        <Column field="title" />
        <Column field="status" widget="statusBadge" />
        <Column field="lead" />
        <Column field="target_date" />
        <Column field="updated_at" />
      </List>
      <Form resource={PROJECT_MODEL} layout="tabs">
        <Field name="title" title />
        <Field name="revision" readOnly hidden />
        <Field name="status" widget="statusbar" options={statusOptions} createOnly />
        <Group label={t("project.group.planning")} columns={2}>
          <Field name="owner" readOnly />
          <Field name="owns_items" />
          <Field name="current_milestone" readOnly />
          <Field name="lead" />
          <Field name="start_date" />
          <Field name="start_date_resolution" options={startResolutionOptions} />
          <Field name="target_date" />
          <Field name="target_date_resolution" options={targetResolutionOptions} />
        </Group>
        <Group label={t("project.group.storage")} columns={2}>
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
    </ResourceList>
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
  const routeHref = useRouteHref();
  const rowActions = useTaskRowActions<TaskActionRow>();
  return (
    <List<TaskActionRow>
      resource={TASK_MODEL}
      scope="local"
      baseFilter={{ project: { exact: recordId } }}
      order={{ sort_order: "ASC" }}
      rowActions={rowActions}
      rowHref={(row) => routeHref.record(TASK_MODEL, row.id)}
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
    <>
      <ProjectPhaseControl recordId={recordId} />
      <ListView<MilestoneRow>
        resource={MILESTONE_MODEL}
        scope="local"
        fields={["id", "name", "description", "start_date", "target_date", "reached_at", "reached_by", "revision", "sort_order"]}
        baseFilter={{ project: { exact: recordId } }}
        order={{ sort_order: "ASC" }}
        columns={[
          { field: "name" },
          { field: "start_date" },
          { field: "target_date" },
          { field: "reached_at" },
          { field: "reached_by" },
          { field: "sort_order" },
        ]}
        rowActions={rowActions}
        emptyContent={t("project.empty.milestones")}
      />
    </>
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
