import * as React from "react";
import { Column, List, ResourceList, containerContents, useContainer } from "@angee/ui";

import { useTagsT } from "../i18n";
import { tagForm } from "./TagForm";

const TAG_MODEL = "tags.Tag";

/**
 * The tag vocabulary — including its shared attention category — is a plain resource page. Scope-specific
 * addons may contribute their own facet/column/field declarations through `tags.tags#facets`, `#columns` and `#fields`.
 */
export function TagsPage(): React.ReactElement {
  const t = useTagsT();
  const scopeFacetEntries = useContainer("tags.tags#facets");
  const scopeColumnEntries = useContainer("tags.tags#columns");
  return (
    <ResourceList resource={TAG_MODEL} form={tagForm} placement="inline" routed>
      <List resource={TAG_MODEL} defaultGroup={{ field: "is_archived" }}>
        {containerContents(scopeFacetEntries)}
        <Column field="name" header={t("col.name")} />
        <Column field="color" header={t("col.color")} />
        <Column field="attention" header={t("col.attention")} />
        {containerContents(scopeColumnEntries)}
        <Column field="updated_at" />
      </List>
    </ResourceList>
  );
}
