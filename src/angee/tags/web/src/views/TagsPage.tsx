import * as React from "react";
import { Column, Facet, List, ResourceList, containerContents, useContainer } from "@angee/ui";

import { useTagsT } from "../i18n";
import { tagForm } from "./TagForm";

const TAG_MODEL = "tags.Tag";

/**
 * The tag vocabulary — each tag's category and attention flag included — is a plain resource page, filterable by
 * category. Scope-specific addons may contribute their own facet/column/field declarations through
 * `tags.tags#facets`, `#columns` and `#fields`.
 */
export function TagsPage(): React.ReactElement {
  const t = useTagsT();
  const scopeFacetEntries = useContainer("tags.tags#facets");
  const scopeColumnEntries = useContainer("tags.tags#columns");
  return (
    <ResourceList resource={TAG_MODEL} form={tagForm} placement="inline" routed>
      <List resource={TAG_MODEL}>
        <Facet field="category" label={t("col.category")} />
        {containerContents(scopeFacetEntries)}
        <Column field="name" header={t("col.name")} />
        <Column field="category" header={t("col.category")} />
        <Column field="color" header={t("col.color")} />
        <Column field="attention" header={t("col.attention")} />
        {containerContents(scopeColumnEntries)}
        <Column field="updated_at" />
      </List>
    </ResourceList>
  );
}
