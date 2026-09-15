import { useAuthoredQuery } from "@angee/refine";
import { EmptyState, ErrorBanner, LoadingPanel, errorMessage, useRecordChromeContext, useT } from "@angee/ui";
import type { ReactElement } from "react";

import { ExtractionRecordEvidenceDocument } from "./documents";
import { ExtractionEvidenceDetails } from "./ExtractionEvidenceDetails";

export function ExtractionEvidencePanel(): ReactElement {
  const { recordId } = useRecordChromeContext();
  const t = useT("workflowsExtraction");
  const query = useAuthoredQuery(ExtractionRecordEvidenceDocument, { id: recordId }, { models: ["workflows_extraction.Extraction"] });
  const evidence = query.data?.extraction_evidence;
  if (query.isFetching && !evidence) return <LoadingPanel message={t("loading")} />;
  if (query.error) return <ErrorBanner description={errorMessage(query.error, t("unavailable"))} />;
  if (!evidence) return <EmptyState icon="workflow-run" title={t("unavailable")} />;
  return <ExtractionEvidenceDetails key={recordId} evidence={evidence} />;
}
