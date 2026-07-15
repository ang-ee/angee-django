import * as React from "react";
import { Column, Field, Form, Group, List, ResourceList, slotContents, useSlot } from "@angee/ui";

import { useTagsT } from "../i18n";
import {
  TAG_SCOPE_COLUMN_SLOT,
  TAG_SCOPE_FACET_SLOT,
  TAG_SCOPE_FIELD_SLOT,
} from "../slots";

const TAG_MODEL = "tags.Tag";

/**
 * The tag vocabulary — a plain resource page for shared tags. Scope-specific
 * addons may contribute their own facet/column/field declarations through slots.
 */
export function TagsPage(): React.ReactElement {
  const t = useTagsT();
  const scopeFacetEntries = useSlot(TAG_SCOPE_FACET_SLOT);
  const scopeColumnEntries = useSlot(TAG_SCOPE_COLUMN_SLOT);
  const scopeFieldEntries = useSlot(TAG_SCOPE_FIELD_SLOT);
  return (
    <ResourceList resource={TAG_MODEL} placement="inline" routed>
      <List resource={TAG_MODEL} defaultGroup={{ field: "is_archived" }}>
        {slotContents(scopeFacetEntries)}
        <Column field="name" header={t("col.name")} />
        <Column field="color" header={t("col.color")} />
        {slotContents(scopeColumnEntries)}
        <Column field="updated_at" />
      </List>
      <Form resource={TAG_MODEL}>
        <Field name="name" title />
        <Group label={t("form.details")} columns={2}>
          <Field name="color" label={t("col.color")} />
          {slotContents(scopeFieldEntries)}
        </Group>
        <Field name="is_archived" />
      </Form>
    </ResourceList>
  );
}
