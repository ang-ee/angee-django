import { PROJECT_MODEL } from "@angee/projects";
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
  useRouteHref,
  type RecordPanelContext,
  type RecordTabDescriptor,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import { usePortfolioT } from "../i18n";
import { PRODUCT_MODEL, RELEASE_MODEL } from "../resources";

/** The complete Product form surface; phasal subjects never gain delivery facts. */
export const PRODUCT_FORM_FIELDS = {
  name: "name",
  lifecycle: "lifecycle",
  owner: "owner",
  originatedFrom: "originated_from",
  body: "body",
} as const;

interface ProjectRow extends StringIdRow {
  title?: unknown;
}

/** Product collection and phasal record surface with projects and releases. */
export function ProductsPage(): React.ReactElement {
  const t = usePortfolioT();
  const lifecycleOptions = useEnumOptions(PRODUCT_MODEL, "lifecycle");
  const recordTabs = React.useMemo<readonly RecordTabDescriptor[]>(
    () => [
      {
        id: "releases",
        label: t("product.tabs.releases"),
        render: (context) => <ProductReleasesTab {...context} />,
      },
      {
        id: "projects",
        label: t("product.tabs.projects"),
        render: (context) => <ProductProjectsTab {...context} />,
      },
    ],
    [t],
  );

  return (
    <ResourceList
      resource={PRODUCT_MODEL}
      placement="inline"
      routed
      recordTabs={recordTabs}
    >
      <List
        resource={PRODUCT_MODEL}
        defaultGroup={{ field: "lifecycle" }}
        order={{ name: "ASC" }}
      >
        <Facet field="owner" label={t("common.owner")} />
        <Column field="name" header={t("common.name")} />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="owner" />
        <Column field="originated_from" />
        <Column field="updated_at" />
      </List>
      <Form resource={PRODUCT_MODEL} layout="tabs">
        <Field name={PRODUCT_FORM_FIELDS.name} title />
        <Field
          name={PRODUCT_FORM_FIELDS.lifecycle}
          widget="statusbar"
          options={lifecycleOptions}
        />
        <Group label={t("product.group.provenance")} columns={2}>
          <Field name={PRODUCT_FORM_FIELDS.owner} />
          <Field name={PRODUCT_FORM_FIELDS.originatedFrom} readOnly />
        </Group>
        <Field name={PRODUCT_FORM_FIELDS.body} widget="markdown.editor" body />
      </Form>
    </ResourceList>
  );
}

function ProductReleasesTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = usePortfolioT();
  const statusOptions = useEnumOptions(RELEASE_MODEL, "status");
  return (
    <DrawerResourceList resource={RELEASE_MODEL} createDefaults={{ product: recordId }}>
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
      <Form resource={RELEASE_MODEL}>
        <Field name="name" title />
        <Field name="status" widget="statusbar" options={statusOptions} />
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

function ProductProjectsTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = usePortfolioT();
  const routeHref = useRouteHref();
  return (
    <List<ProjectRow>
      resource={PROJECT_MODEL}
      scope="local"
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
