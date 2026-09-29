import type { DecisionContentProps } from "@angee/decisions";
import { ComparisonRows, DetailSection, EmptyState, MetaGrid } from "@angee/ui";
import type { ReactElement } from "react";
import * as v from "valibot";

import { useWorkflowsPartiesT } from "./i18n";

const DuplicateBasis = v.object({
  left: v.string(), right: v.string(), left_name: v.string(), right_name: v.string(), evidence: v.string(),
});

/** Compare one mapped pair; merge and veto policy stays with the parties owners. */
export function DuplicatePartyDecisionContent({ basis }: Pick<DecisionContentProps, "basis">): ReactElement {
  const t = useWorkflowsPartiesT();
  const parsed = v.safeParse(DuplicateBasis, basis);
  if (!parsed.success) return <EmptyState title={t("review.unavailable")} description={t("review.unavailableDescription")} />;
  const pair = parsed.output;
  return <DetailSection title={t("duplicate.title")}>
    <ComparisonRows beforeLabel={t("duplicate.left")} afterLabel={t("duplicate.right")} rows={[
      { key: "name", label: t("duplicate.name"), before: pair.left_name, after: pair.right_name },
    ]} />
    <MetaGrid rows={[[t("duplicate.evidence"), pair.evidence]]} />
  </DetailSection>;
}
