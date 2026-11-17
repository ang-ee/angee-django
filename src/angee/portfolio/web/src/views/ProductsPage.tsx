import {
  Column,
  Facet,
  List,
  ResourceList,
} from "@angee/ui";
import * as React from "react";

import { usePortfolioT } from "../i18n";
import { PRODUCT_MODEL } from "../resources";
import { productForm } from "../record-forms";

/** Product collection and phasal record surface with projects and releases. */
export function ProductsPage(): React.ReactElement {
  const t = usePortfolioT();
  return (
    <ResourceList
      resource={PRODUCT_MODEL}
      placement="inline"
      routed
      form={productForm}
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
    </ResourceList>
  );
}
