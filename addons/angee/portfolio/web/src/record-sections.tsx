import { PROJECT_MODEL } from "@angee/projects";
import {
  Column,
  DrawerResourceList,
  Field,
  Form,
  Group,
  List,
  useEnumOptions,
  useRecordChromeContext,
  useRouteHref,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import { usePortfolioT } from "./i18n";
import { INITIATIVE_PROJECT_MODEL, RELEASE_MODEL } from "./resources";

interface ProjectRow extends StringIdRow {
  title?: unknown;
}

/** The initiative's projects, as the record's own `#sections` tab. */
export function InitiativeProjectsTab(): React.ReactElement {
  const t = usePortfolioT();
  const { recordId } = useRecordChromeContext();
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

/** The product's releases, as the record's own `#sections` tab. */
export function ProductReleasesTab(): React.ReactElement {
  const t = usePortfolioT();
  const { recordId } = useRecordChromeContext();
  const statusOptions = useEnumOptions(RELEASE_MODEL, "status");
  return (
    <DrawerResourceList resource={RELEASE_MODEL} presentation="embedded" createDefaults={{ product: recordId }}>
      <List
        resource={RELEASE_MODEL}
        baseFilter={{ product: { exact: recordId } }}
        order={{ target_date: "DESC" }}
        emptyContent={t("product.empty.releases")}
      >
        <Column field="name" header={t("common.name")} />
        <Column field="status" widget="statusBadge" />
        <Column field="target_date" />
        <Column field="shipped_on" />
      </List>
      <Form resource={RELEASE_MODEL} eyebrow={t("release.eyebrow")}>
        <Field name="name" title />
        <Field name="status" widget="statusbar" status options={statusOptions} />
        <Field name="product" readOnly />
        <Group label={t("release.group.dates")} columns={2}>
          <Field name="target_date" />
          <Field name="shipped_on" />
        </Group>
        <Field name="notes" widget="markdown.editor" body />
      </Form>
    </DrawerResourceList>
  );
}

/** The product's projects, as the record's own `#sections` tab. */
export function ProductProjectsTab(): React.ReactElement {
  const t = usePortfolioT();
  const routeHref = useRouteHref();
  const { recordId } = useRecordChromeContext();
  return (
    <List<ProjectRow>
      resource={PROJECT_MODEL}
      presentation="embedded"
      baseFilter={{ product: { exact: recordId } }}
      order={{ sort_order: "ASC" }}
      rowHref={(row) => routeHref("projects.projects.record", { id: row.id })}
      emptyContent={t("product.empty.projects")}
    >
      <Column field="title" />
      <Column field="status" widget="statusBadge" />
      <Column field="health" widget="statusBadge" />
      <Column field="priority" />
      <Column field="target_date" />
    </List>
  );
}
