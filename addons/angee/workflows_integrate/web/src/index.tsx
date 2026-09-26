import { defineBaseAddon } from "@angee/app";
import { INTEGRATION_MODEL } from "@angee/integrate";
import { useAuthoredQuery } from "@angee/refine";
import {
  TextLink,
  formViewRecordActionsSlot,
  useRecordChromeContext,
  useResourceRecordHref,
} from "@angee/ui";
import type { ReactElement } from "react";

import { IntegrationSyncRun } from "./documents";
import { enWorkflowsIntegrateMessages, useWorkflowsIntegrateT } from "./i18n";

/** Read the workflow projection explicitly; child forms need no telemetry fields. */
export function IntegrationSyncRunLink(): ReactElement | null {
  const t = useWorkflowsIntegrateT();
  const { recordId, resource, dataProviderName } = useRecordChromeContext();
  const runHref = useResourceRecordHref("workflows.WorkflowRun");
  const read = useAuthoredQuery(IntegrationSyncRun, { id: recordId }, {
    dataProviderName,
    models: [INTEGRATION_MODEL, resource],
  });
  const run = read.data?.integrations_by_pk?.sync_run;
  const href = run ? runHref?.(run) : undefined;
  return href ? <TextLink href={href}>{t("sync.openRun")}</TextLink> : null;
}

export default defineBaseAddon({
  id: "workflows-integrate",
  i18n: { "workflows-integrate": enWorkflowsIntegrateMessages },
  slots: [{
    ...formViewRecordActionsSlot(INTEGRATION_MODEL),
    id: "workflows-integrate.sync-run",
    sequence: 20,
    content: <IntegrationSyncRunLink />,
  }],
});
