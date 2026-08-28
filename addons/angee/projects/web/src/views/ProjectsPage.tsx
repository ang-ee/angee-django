import {
  Column,
  Facet,
  Field,
  Form,
  Group,
  List,
  ListView,
  ResourceList,
  useEnumOptions,
  useRouteHref,
  type RecordPanelContext,
  type RecordTabDescriptor,
} from "@angee/ui";
import * as React from "react";

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
        <Field name="status" widget="statusbar" options={statusOptions} createOnly />
        <Group label={t("project.group.planning")} columns={2}>
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
      </Form>
    </ResourceList>
  );
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
      rowHref={(row) => routeHref("projects.tasks.record", { id: row.id })}
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

function ProjectMilestonesTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useProjectsT();
  return (
    <ListView
      resource={MILESTONE_MODEL}
      scope="local"
      fields={["id", "name", "description", "target_date", "sort_order"]}
      baseFilter={{ project: { exact: recordId } }}
      order={{ sort_order: "ASC" }}
      columns={[
        { field: "name" },
        { field: "target_date" },
        { field: "sort_order" },
      ]}
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
