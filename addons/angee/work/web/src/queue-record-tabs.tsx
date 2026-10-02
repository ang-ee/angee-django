import * as React from "react";
import { Skeleton, SkeletonStatus, type RecordPanelContext, type RecordTabDescriptor } from "@angee/ui";

import { useWorkT } from "./i18n";

// The tab body lives with the page; a host that composes the tab loads it on first use, so the page
// stays out of the manifest's eager import graph.
const QueueStagesTab = React.lazy(() => import("./views/QueuesPage").then((module) => ({ default: module.QueueStagesTab })));

/** The queue record's tabs (stages), for the page and for a host that shows a queue on its own. */
export function useQueueRecordTabs(): readonly RecordTabDescriptor[] {
  const t = useWorkT();
  return React.useMemo<readonly RecordTabDescriptor[]>(() => [
    {
      id: "stages",
      label: t("queue.stages.tab"),
      render: (context: RecordPanelContext) => <React.Suspense fallback={<SkeletonStatus label={t("queue.stages.tab")} className="grid gap-3 py-4"><Skeleton className="h-8 w-1/2" /><Skeleton className="h-24" /></SkeletonStatus>}>
        <QueueStagesTab {...context} />
      </React.Suspense>,
    },
  ], [t]);
}
