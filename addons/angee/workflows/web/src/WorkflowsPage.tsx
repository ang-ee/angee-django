import type { ReactElement } from "react";
import { Column, List, ResourceList, useRouteHref, type StringIdRow } from "@angee/ui";

import { WORKFLOW_MODEL } from "./catalogue/documents.console";
import { useWorkflowsT } from "./i18n";

/** Readable workflow identities; publication and authoring remain backend-owned. */
export function WorkflowsPage(): ReactElement {
  const t = useWorkflowsT();
  const href = useRouteHref();
  return <ResourceList<StringIdRow> resource={WORKFLOW_MODEL} hideCreate
    rowHref={(row) => href("workflows.catalogue.record", { id: row.id })} emptyContent={t("catalogue.empty")}>
    <List resource={WORKFLOW_MODEL}>
      <Column field="key" header={t("catalogue.key")} />
      <Column field="name" header={t("catalogue.name")} />
      <Column field="subject_model" header={t("catalogue.subjectModel")} />
      <Column field="published.number" header={t("catalogue.publishedVersion")} />
    </List>
  </ResourceList>;
}
