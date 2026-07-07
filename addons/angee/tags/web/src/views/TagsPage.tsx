import * as React from "react";
import { Column, Facet, Field, Form, Group, List, ResourceList } from "@angee/ui";

import { useTagsT } from "../i18n";

const TAG_MODEL = "tags.Tag";

/**
 * The tag vocabulary — a plain resource page for the shared/company-scoped tags.
 * `company` is nullable (a shared tag when omitted), so the list facets by scope
 * and archive state; the polymorphic assignments themselves are edited per-record
 * through the tags chatter pane, not here.
 */
export function TagsPage(): React.ReactElement {
  const t = useTagsT();
  return (
    <ResourceList resource={TAG_MODEL} placement="inline" routed>
      <List resource={TAG_MODEL} defaultGroup={{ field: "is_archived" }}>
        <Facet field="company" label={t("col.scope")} labelField="name" />
        <Column field="name" header={t("col.name")} />
        <Column field="color" header={t("col.color")} />
        <Column field="company" header={t("col.scope")} />
        <Column field="updated_at" />
      </List>
      <Form resource={TAG_MODEL}>
        <Field name="name" title />
        <Group label={t("form.details")} columns={2}>
          <Field name="color" label={t("col.color")} />
          <Field name="company" label={t("col.scope")} />
        </Group>
        <Field name="is_archived" />
      </Form>
    </ResourceList>
  );
}
