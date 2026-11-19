import * as React from "react";
import { Column, Field, Form, Group, List, ResourceList } from "@angee/ui";

import { useTagsT } from "../i18n";

const CATEGORY_MODEL = "tags.TagCategory";

/**
 * Tag categories: the ordered groups the vocabulary files its tags under. A
 * category's maximum caps how many of its tags one record holds; the server
 * refuses a write that would exceed it. Lists read in category order.
 */
export function TagCategoriesPage(): React.ReactElement {
  const t = useTagsT();
  return (
    <ResourceList resource={CATEGORY_MODEL} placement="inline" routed>
      <List resource={CATEGORY_MODEL}>
        <Column field="name" header={t("col.name")} />
        <Column field="max_per_record" header={t("col.maxPerRecord")} />
        <Column field="position" />
        <Column field="updated_at" />
      </List>
      <Form resource={CATEGORY_MODEL}>
        <Field name="name" title />
        <Group label={t("form.details")} columns={2}>
          <Field name="max_per_record" label={t("col.maxPerRecord")} />
          <Field name="position" />
        </Group>
        <Field name="description" />
      </Form>
    </ResourceList>
  );
}
