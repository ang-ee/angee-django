import { useCallback, useMemo } from "react";
import { cn } from "../../lib/cn";
import { useUiT } from "../../i18n";
import { Gantt, type GanttProps } from "./gantt";
import { GanttView as GanttGrid } from "./gantt-view";
import { GanttNav, GanttNavToday, GanttNavPrev, GanttNavNext, GanttTitle, GanttScaleSwitcher } from "./gantt-nav";
import type { GanttI18nConfig } from "./gantt-i18n";
import type { GanttViewProps } from "./GanttView";

const READ_ONLY = { drag: false, resize: false, selectSlot: false };

export default function GanttSurface({ resources, events, renderRowContent, onEventClick, className, ...props }: GanttViewProps) {
  const t = useUiT();
  const labels = useMemo<GanttI18nConfig["labels"]>(() => ({
    today: t("gantt.today"), previous: t("gantt.previous"), next: t("gantt.next"),
    addEvent: t("gantt.addEvent"), addTask: t("gantt.addTask"), allDay: t("gantt.allDay"),
    loading: t("gantt.loading"), event: t("gantt.event"), resources: t("gantt.resources"),
    events: (count) => t("gantt.events", { count }),
    week: (count) => t("gantt.weekNumber", { count }), goToDate: t("gantt.goToDate"),
    scheduleHint: t("gantt.scheduleHint"), scheduleHintDrag: t("gantt.scheduleHintDrag"),
    reorder: t("gantt.reorder"), selectView: t("gantt.selectView"),
    zoomIn: t("gantt.zoomIn"), zoomOut: t("gantt.zoomOut"), resizePanel: t("gantt.resizePanel"),
    jumpToBar: (title) => t("gantt.jumpToBar", { title }),
    progress: (percent) => t("gantt.progress", { percent }),
    durationDays: (count) => t("gantt.days", { count }),
    continues: t("gantt.continues"), planned: (range) => t("gantt.planned", { range }),
    milestone: t("gantt.milestone"),
    scales: { day: t("gantt.day"), week: t("gantt.week"), month: t("gantt.month"), quarter: t("gantt.quarter"), year: t("gantt.year") },
  }), [t]);
  const i18n = useMemo(() => ({ labels }), [labels]);
  const renderNoResources = useCallback(() => t("gantt.empty"), [t]);
  const renderResourceLabel = useCallback<NonNullable<GanttProps["renderResourceLabel"]>>(({ resource }) => (
    <span className="flex min-w-0 items-center gap-2">
      <span className="truncate">{resource.title}</span>
      {renderRowContent?.(resource)}
    </span>
  ), [renderRowContent]);
  const handleEventClick = useCallback<NonNullable<GanttProps["onEventClick"]>>(
    (occurrence) => onEventClick?.(occurrence.event), [onEventClick],
  );
  return (
    <Gantt
      {...props}
      className={cn("h-full flex-1", className)}
      resources={resources}
      events={events}
      interactions={READ_ONLY}
      i18n={i18n}
      rowCheckboxes={false}
      scheduleMode="multiple"
      initialCenter="anchor"
      renderNoResources={renderNoResources}
      renderResourceLabel={renderResourceLabel}
      onEventClick={onEventClick ? handleEventClick : undefined}
    >
      <GanttNav>
        <GanttNavToday />
        <GanttNavPrev />
        <GanttNavNext />
        <GanttTitle className="min-w-0 flex-1" />
        <GanttScaleSwitcher />
      </GanttNav>
      <GanttGrid />
    </Gantt>
  );
}
