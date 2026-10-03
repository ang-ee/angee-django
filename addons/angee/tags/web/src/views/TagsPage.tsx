import * as React from "react";
import { Column, Field, Form, Group, List, ResourceList, containerContents, useContainer } from "@angee/ui";

import { useTagsT } from "../i18n";

const TAG_MODEL = "tags.Tag";

/**
 * The tag vocabulary — a plain resource page for shared tags. Scope-specific
 * addons may contribute their own facet/column/field declarations through `tags.tags#facets`, `#columns` and `#fields`.
 */
export function TagsPage(): React.ReactElement {
  const t = useTagsT();
  const scopeFacetEntries = useContainer("tags.tags#facets");
  const scopeColumnEntries = useContainer("tags.tags#columns");
  const scopeFieldEntries = useContainer("tags.tags#fields");
  return (
    <ResourceList resource={TAG_MODEL} placement="inline" routed>
      <List resource={TAG_MODEL} defaultGroup={{ field: "is_archived" }}>
        {containerContents(scopeFacetEntries)}
        <Column field="name" header={t("col.name")} />
        <Column field="color" header={t("col.color")} />
        {containerContents(scopeColumnEntries)}
        <Column field="updated_at" />
      </List>
      <Form resource={TAG_MODEL}>
        <Field name="name" title />
        <Group label={t("form.details")} columns={2}>
          <Field name="color" label={t("col.color")} />
          {containerContents(scopeFieldEntries)}
        </Group>
        <Field name="is_archived" />
      </Form>
    </ResourceList>
  );
}
