import * as React from "react";
import { Column, Facet, List, ResourceList } from "@angee/ui";
import { IntegrationSyncColumns } from "@angee/integrate";

import { useIntegrateVcsT } from "../i18n";
import { vcsBridgeForm } from "./VcsBridgeForm";

const MODEL = "integrate_vcs.VcsBridge";

/**
 * VCS bridges own repository discovery and source sync for one integration child row.
 */
export function VcsBridgesPage(): React.ReactElement {
  const t = useIntegrateVcsT();
  return (
    <ResourceList resource={MODEL} form={vcsBridgeForm} placement="inline" routed>
      <List resource={MODEL}>
        <Facet field="vendor" label={t("col.vendor")} />
        <Column field="display_name" />
        <Column field="backend_class" header={t("vcs.backendClass")} />
        <Column field="lifecycle" header={t("col.lifecycle")} widget="statusBadge" />
        <Column field="runtime_status" header={t("col.runtimeStatus")} widget="colorDot" />
        {IntegrationSyncColumns({ fields: ["sync_stage", "last_sync_completed_at"] })}
      </List>
    </ResourceList>
  );
}
