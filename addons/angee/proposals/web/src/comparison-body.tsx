import { EmptyState, ErrorBanner, Skeleton, SkeletonStatus } from "@angee/ui";
import type { ReactElement } from "react";

import { useRoundComparisonData } from "./comparison-data";
import { RoundComparisonGrid } from "./comparison-grid";
import { useProposalsT } from "./i18n";

/** The comparison body shared by record tab and full page. */
export function RoundComparisonBody({ data, proposalHref }: {
  data: ReturnType<typeof useRoundComparisonData>;
  proposalHref?: (proposalId: string) => string | undefined;
}): ReactElement {
  const t = useProposalsT();
  if (data.error) return <ErrorBanner title={t("comparison.error")} description={data.error.message} />;
  if (data.fetching && !data.round) return <SkeletonStatus label={t("comparison.loading")} className="grid gap-3 p-5">
    <Skeleton shape="text" className="h-4 w-2/3" />
    <Skeleton className="h-32 w-full" />
  </SkeletonStatus>;
  if (!data.proposals.length) return <EmptyState fill icon="proposals-round"
    title={t("comparison.empty.title")} description={t("comparison.empty.description")} />;
  return <RoundComparisonGrid topics={data.topics} proposals={data.proposals}
    answers={data.answers} proposalHref={proposalHref} />;
}
