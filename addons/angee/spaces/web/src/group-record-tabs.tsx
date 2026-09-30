import * as React from "react";
import { Skeleton, SkeletonStatus, type RecordPanelContext, type RecordTabDescriptor } from "@angee/ui";

import { useSpacesT } from "./i18n";

// The tab bodies live with the page; a host that composes the tabs loads them on first use, so the
// page stays out of the manifest's eager import graph.
const GroupRosterTab = React.lazy(() => import("./SpacesPage").then((module) => ({ default: module.GroupRosterTab })));
const GroupThreadsTab = React.lazy(() => import("./SpacesPage").then((module) => ({ default: module.GroupThreadsTab })));

function Deferred({ label, children }: { label: string; children: React.ReactNode }): React.ReactElement {
  return <React.Suspense fallback={<SkeletonStatus label={label} className="grid gap-3 py-4"><Skeleton className="h-8 w-1/2" /><Skeleton className="h-24" /></SkeletonStatus>}>
    {children}
  </React.Suspense>;
}

/** The group record's tabs (roster, threads), for the page and for a host that shows a group on its own. */
export function useGroupRecordTabs(): readonly RecordTabDescriptor[] {
  const t = useSpacesT();
  return React.useMemo<readonly RecordTabDescriptor[]>(() => [
    {
      id: "roster",
      label: t("group.tabs.roster"),
      render: (context: RecordPanelContext) => <Deferred label={t("group.tabs.roster")}><GroupRosterTab key={context.recordId} {...context} /></Deferred>,
    },
    {
      id: "threads",
      label: t("group.tabs.threads"),
      render: (context: RecordPanelContext) => <Deferred label={t("group.tabs.threads")}><GroupThreadsTab {...context} /></Deferred>,
    },
  ], [t]);
}
