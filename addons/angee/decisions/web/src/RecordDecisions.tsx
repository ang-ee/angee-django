import type { ResourceFilter } from "@angee/metadata";
import { ListView, Tab, useRecordChromeContext } from "@angee/ui";
import type { ReactElement } from "react";

import { useDecisionsT } from "./i18n";
import { DECISION_MODEL } from "./documents.console";

/** Readable decisions for a subject or step run compose the shared embedded list. */
export function DecisionsList({ baseFilter }: { baseFilter: ResourceFilter<string> }): ReactElement {
  const t = useDecisionsT();
  return <ListView resource={DECISION_MODEL} presentation="embedded" scope="local"
    fields={["id", "kind", "verdict", "resolved_at"]} baseFilter={baseFilter}
    order={{ created_at: "DESC" }} columns={[
      { field: "kind", header: t("decisions.kind") },
      { field: "verdict", header: t("decisions.verdict"), widget: "statusBadge" },
      { field: "resolved_at", header: t("decisions.resolved") },
    ]} />;
}

export function RecordDecisions(): ReactElement {
  const { resource, recordId } = useRecordChromeContext();
  return <DecisionsList baseFilter={{ subject_model: { exact: resource },
    subject_id: { exact: recordId } }} />;
}

function DecisionsLabel(): ReactElement {
  const t = useDecisionsT();
  return <>{t("decisions.label")}</>;
}

/**
 * The generic Decision.subject tab, for a subject model's `#sections`: an addon
 * opts its model in under its own id, `"<addon>.decisions"`.
 */
export function decisionRecordTab() {
  return {
    sequence: 50,
    content: <Tab id="decisions" label={<DecisionsLabel />}><RecordDecisions /></Tab>,
  };
}
