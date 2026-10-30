import {
  Column,
  Facet,
  Field,
  Form,
  Group,
  List,
  ResourceList,
  useEnumOptions,
} from "@angee/ui";
import * as React from "react";

import { usePortfolioT } from "../i18n";
import { INITIATIVE_MODEL } from "../resources";

/** Initiative hierarchy rendered as a parent-grouped tree-compatible list. */
export function InitiativesPage(): React.ReactElement {
  const t = usePortfolioT();
  const statusOptions = useEnumOptions(INITIATIVE_MODEL, "status");
  const healthOptions = useEnumOptions(INITIATIVE_MODEL, "health");
  const resolutionOptions = useEnumOptions(
    INITIATIVE_MODEL,
    "target_date_resolution",
  );
  return (
    <ResourceList
      resource={INITIATIVE_MODEL}
      placement="inline"
      routed
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
      <Form resource={INITIATIVE_MODEL} eyebrow={t("initiative.eyebrow")}>
        <Field name="name" title />
        <Field name="status" widget="statusbar" status options={statusOptions} />
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

