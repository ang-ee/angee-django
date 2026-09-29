import { DecisionsList } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import { ErrorBanner, LoadingPanel, useRecordChromeContext } from "@angee/ui";

import { TaskAccessDecisionsDocument } from "./documents";
import { useIntakeT } from "./i18n";
import { NEED_MODEL } from "./resources";

/** Intake owns the Need-to-Task association; decisions owns the seats and verbs. */
export function TaskAccessDecisions() {
  const t = useIntakeT();
  const { recordId, dataProviderName } = useRecordChromeContext();
  const query = useAuthoredQuery(TaskAccessDecisionsDocument, { task: recordId }, {
    dataProviderName, models: [NEED_MODEL, "decisions.Decision"],
  });
  if (query.error) return <ErrorBanner title={t("access.error")} description={query.error.message} />;
  if (!query.data) return <LoadingPanel />;
  return <DecisionsList ids={query.data.intake_needs.flatMap((need) => need.access_decision ? [need.access_decision.id] : [])} />;
}
