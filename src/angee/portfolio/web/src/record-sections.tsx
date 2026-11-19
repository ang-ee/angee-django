import { PROJECT_MODEL } from "@angee/projects";
import {
  Column,
  DrawerResourceList,
  List,
  RecordActionBar,
  useActionResultRun,
  useRecordChromeActionOutcome,
  useRecordChromeContext,
  useRouteHref,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import { initiativeProjectForm, releaseForm } from "./record-forms";
import { PromoteProjectToProductDocument } from "./documents";
import { usePortfolioT } from "./i18n";
import { INITIATIVE_PROJECT_MODEL, PRODUCT_MODEL, RELEASE_MODEL } from "./resources";

/** Portfolio's maturation verb joins the project's existing record menu. */
export function PromoteProjectAction(): React.ReactElement | null {
  const t = usePortfolioT();
  const { record, recordId, formReadOnly } = useRecordChromeContext();
  const [promote, state] = useRecordChromeActionOutcome({
    document: PromoteProjectToProductDocument,
    resultField: "promote_project_to_product",
    idArgument: "id",
  }, { invalidateModels: [PRODUCT_MODEL] });
  const settle = useActionResultRun({ linkTo: PRODUCT_MODEL });
  if (!record || !recordId || formReadOnly) return null;
  return <RecordActionBar record={record} actions={[{
    id: "portfolio-promote-project",
    label: t("project.promote"),
    permission: "write",
    placement: "menu",
    disabled: state.fetching,
    run: async () => { await settle(() => promote(recordId)); },
  }]} />;
}

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
      form={initiativeProjectForm}
      presentation="embedded" chrome={{ heading: false }}
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
      </List>
    </DrawerResourceList>
  );
}

/** The product's releases, as the record's own `#sections` tab. */
export function ProductReleasesTab(): React.ReactElement {
  const t = usePortfolioT();
  const { recordId } = useRecordChromeContext();
  return (
    <DrawerResourceList resource={RELEASE_MODEL} form={releaseForm} presentation="embedded" chrome={{ heading: false }} createDefaults={{ product: recordId }}>
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
      chrome={{ heading: false }}
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
