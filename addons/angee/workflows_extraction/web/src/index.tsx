import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Tab, formViewSectionsSlot, useT } from "@angee/ui";
import { ExtractionEvidencePanel } from "./ExtractionEvidencePanel";

import { enWorkflowsExtractionMessages } from "./i18n";

export { ExtractionRecordEvidenceDocument } from "./documents";
export { ExtractionEvidenceDetails } from "./ExtractionEvidenceDetails";

export default defineBaseAddon({
  id: "workflows-extraction",
  routes: resourcePageRoutes(
    "workflows-extraction.extractions",
    "/workflows/extractions",
    lazyRouteComponent(() => import("./ExtractionsPage"), "ExtractionsPage"),
    "workflows_extraction.Extraction",
  ),
  i18n: { workflowsExtraction: enWorkflowsExtractionMessages },
  slots: [{
    ...formViewSectionsSlot("workflows_extraction.Extraction"),
    id: "workflows-extraction.evidence",
    sequence: 10,
    content: <Tab id="evidence" label={<EvidenceLabel />}><ExtractionEvidencePanel /></Tab>,
  }],
});

function EvidenceLabel() {
  const t = useT("workflowsExtraction");
  return <>{t("evidence")}</>;
}
