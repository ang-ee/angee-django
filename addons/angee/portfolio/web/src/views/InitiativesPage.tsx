import {
  Column,
  DrawerResourceList,
  Facet,
  Field,
  Form,
  Group,
  List,
  ResourceList,
  useEnumOptions,
  type RecordPanelContext,
  type RecordTabDescriptor,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import { usePortfolioT } from "../i18n";
import { INITIATIVE_MODEL, INITIATIVE_PROJECT_MODEL } from "../resources";

/** Initiative hierarchy rendered as a parent-grouped tree-compatible list. */
export function InitiativesPage(): React.ReactElement {
  const t = usePortfolioT();
  const statusOptions = useEnumOptions(INITIATIVE_MODEL, "status");
  const healthOptions = useEnumOptions(INITIATIVE_MODEL, "health");
  const resolutionOptions = useEnumOptions(
    INITIATIVE_MODEL,
    "target_date_resolution",
  );
  const recordTabs = React.useMemo<readonly RecordTabDescriptor[]>(
    () => [
      {
        id: "projects",
        label: t("initiative.tabs.projects"),
        render: (context) => <InitiativeProjectsTab {...context} />,
      },
    ],
    [t],
  );

  return (
    <ResourceList
      resource={INITIATIVE_MODEL}
      placement="inline"
      routed
      recordTabs={recordTabs}
    >
      <List
        resource={INITIATIVE_MODEL}
        defaultGroup={{ field: "parent" }}
        order={{ sort_order: "ASC" }}
      >
        <Facet field="status" />
        <Facet field="health" />
        <Column field="name" header={t("common.name")} />
        <Column field="status" widget="statusBadge" />
        <Column field="health" widget="statusBadge" />
        <Column field="owner" />
        <Column field="target_date" />
        <Column field="priority" />
      </List>
      <Form resource={INITIATIVE_MODEL} layout="tabs">
        <Field name="name" title />
        <Field name="status" widget="statusbar" options={statusOptions} />
        <Group label={t("initiative.group.identity")} columns={2}>
          <Field name="owner" />
          <Field name="parent" />
          <Field name="health" widget="statusBadge" options={healthOptions} readOnly />
          <Field name="health_updated_at" readOnly />
        </Group>
        <Group label={t("initiative.group.delivery")} columns={2}>
          <Field name="target_date" />
          <Field name="target_date_resolution" options={resolutionOptions} />
          <Field name="priority" />
          <Field name="sort_order" label={t("common.order")} />
        </Group>
        <Field name="body" widget="markdown.editor" body />
      </Form>
    </ResourceList>
  );
}

function InitiativeProjectsTab({
  recordId,
}: RecordPanelContext): React.ReactElement {
  const t = usePortfolioT();
  return (
    <DrawerResourceList
      resource={INITIATIVE_PROJECT_MODEL}
      createDefaults={{ initiative: recordId }}
    >
      <List<StringIdRow>
        resource={INITIATIVE_PROJECT_MODEL}
        baseFilter={{ initiative: { exact: recordId } }}
        order={{ sort_order: "ASC" }}
        emptyContent={t("initiative.empty.projects")}
      >
        <Column field="project" header={t("common.project")} />
        <Column field="project.product" />
        <Column field="project.status" widget="statusBadge" />
        <Column field="project.health" widget="statusBadge" />
        <Column field="sort_order" header={t("common.order")} />
      </List>
      <Form resource={INITIATIVE_PROJECT_MODEL}>
        <Field name="project" title />
        <Field name="initiative" readOnly />
        <Field name="sort_order" label={t("common.order")} />
      </Form>
    </DrawerResourceList>
  );
}
