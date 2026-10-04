import { useAuthoredQuery } from "@angee/refine";
import { DecisionCard } from "./DecisionCard";
import type { ResourceFilter } from "@angee/metadata";
import { ListView, Tab, LoadingPanel, ErrorBanner, useRecordChromeContext } from "@angee/ui";
import type { ReactElement } from "react";

import { useDecisionsT } from "./i18n";
import { DECISION_MODEL, DECISION_MODELS, OpenDecisionsDocument } from "./documents.console";

/** A caller's readable decision selection composes the shared embedded list. */
export function DecisionsList({ baseFilter }: { baseFilter: ResourceFilter<string> }): ReactElement {
  const t = useDecisionsT();
  return <ListView resource={DECISION_MODEL} presentation="embedded" scope="local"
    fields={["id", "kind", "verdict", "answered_at"]} baseFilter={baseFilter}
    order={{ created_at: "DESC" }} columns={[
      { field: "kind", header: t("decisions.kind") },
      { field: "verdict", header: t("decisions.verdict") },
      { field: "answered_at", header: t("decisions.answered") },
    ]} />;
}

export function RecordDecisions(): ReactElement {
  const { resource, recordId } = useRecordChromeContext();
  const query = useAuthoredQuery(OpenDecisionsDocument, { model: resource, id: recordId }, { models: DECISION_MODELS });
  const t = useDecisionsT();
  if (query.isLoading) return <LoadingPanel />;
  if (query.error) return <ErrorBanner description={t("decision.unavailable")} />;
  return <div className="space-y-4">{query.data?.open_decisions.map((decision) =>
    <DecisionCard key={decision.id} decision={decision} onAnswered={query.refetch} />)}</div>;
}

function DecisionsLabel(): ReactElement {
  const t = useDecisionsT();
  return <>{t("decisions.label")}</>;
}

/**
 * The generic record decisions tab, for a model's `#sections`: an addon
 * opts its model in under its own id, `"<addon>.decisions"`.
 */
export function decisionRecordTab() {
  return {
    sequence: 50,
    content: <Tab id="decisions" label={<DecisionsLabel />}><RecordDecisions /></Tab>,
  };
}
