import { lazy, useMemo, useState, type ReactNode } from "react";
import { LazyBoundary } from "../../fragments/LazyBoundary";
import { LoadingPanel } from "../../fragments/LoadingPanel";
import { Button } from "../../ui/button";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { useUiT } from "../../i18n";
import type { GanttEvent, GanttResource, GanttScale } from "./gantt-types";

export type { GanttEvent, GanttResource, GanttScale } from "./gantt-types";

export interface GanttViewProps {
  resources: readonly GanttResource[];
  events: readonly GanttEvent[];
  date?: Date;
  defaultDate?: Date;
  onDateChange?: (date: Date) => void;
  defaultScale?: GanttScale;
  loading?: boolean;
  nowIndicator?: boolean;
  renderRowContent?: (resource: GanttResource) => ReactNode;
  onEventClick?: (event: GanttEvent) => void;
  className?: string;
}

/** Read-only date-scaled schedules. The drawing library loads only on mount. */
export function GanttView(props: GanttViewProps) {
  const t = useUiT();
  const [attempt, setAttempt] = useState(0);
  // React.lazy retains a rejected import: a retry needs both a fresh lazy
  // component and a reset boundary, while ordinary renders keep their identity.
  const GanttSurface = useMemo(() => lazy(() => import("./gantt-surface")), [attempt]);
  return (
    <LazyBoundary resetKey={attempt} pending={<LoadingPanel />} fallback={
      <ErrorBanner description={t("gantt.loadFailed")}
        actions={<Button size="sm" onClick={() => setAttempt((current) => current + 1)}>{t("collection.retry")}</Button>} />
    }>
      <GanttSurface {...props} />
    </LazyBoundary>
  );
}
