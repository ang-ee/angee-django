import {
  Column,
  Facet,
  List,
  ResourceList,
} from "@angee/ui";
import * as React from "react";

import { usePortfolioT } from "../i18n";
import { INITIATIVE_MODEL } from "../resources";
import { initiativeForm } from "../record-forms";

/** Initiative hierarchy rendered as a parent-grouped tree-compatible list. */
export function InitiativesPage(): React.ReactElement {
  const t = usePortfolioT();
  return (
    <ResourceList
      resource={INITIATIVE_MODEL}
      placement="inline"
      routed
      form={initiativeForm}
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
    </ResourceList>
  );
}
