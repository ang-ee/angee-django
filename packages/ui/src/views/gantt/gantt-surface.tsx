import { useCallback, useMemo, useState } from "react";
import { addWeeks, startOfDay, startOfWeek, subMilliseconds } from "date-fns";
import { cn } from "../../lib/cn";
import { useUiT } from "../../i18n";
import { Gantt, type GanttProps } from "./gantt";
import { GanttView as GanttGrid } from "./gantt-view";
import { GanttNav, GanttNavToday, GanttNavPrev, GanttNavNext, GanttTitle, GanttScaleSwitcher } from "./gantt-nav";
import { Button } from "../../ui/button";
import { Glyph } from "../../chrome/Glyph";
import type { GanttI18nConfig } from "./gantt-i18n";
import type { GanttViewProps } from "./GanttView";

const READ_ONLY = { drag: false, resize: false, selectSlot: false };

export default function GanttSurface({ resources, events, renderRowContent, laneHeader, onEventClick, onResourceClick, selectedRows, onSelectedRowsChange, className,
  sidebarWidth = 224, minRowHeight = 3.5, fitToEvents = false, onDateChange, ...props }: GanttViewProps) {
  const t = useUiT();
  const [today] = useState(() => startOfDay(new Date()));
  const [navigated, setNavigated] = useState(false);
  const [zoom, setZoom] = useState(1);
  const fitting = fitToEvents && !navigated;
  const range = useMemo(() => {
    if (!fitting) return undefined;
    let first = today;
    let last = today;
    for (const event of events) {
      if (event.start < first) first = event.start;
      const end = event.end > event.start ? subMilliseconds(event.end, 1) : event.end;
      if (end > last) last = end;
    }
    return { start: startOfWeek(first, { weekStartsOn: 1 }), end: addWeeks(startOfWeek(last, { weekStartsOn: 1 }), 2) };
  }, [fitting, events, today]);
  const handleDateChange = useCallback((date: Date) => {
    setNavigated(true);
    onDateChange?.(date);
  }, [onDateChange]);
  const handleScaleChange = useCallback(() => setNavigated(true), []);
  const treePanel = useMemo(() => ({ width: sidebarWidth, minWidth: sidebarWidth, maxWidth: sidebarWidth,
    nameColumnWidth: sidebarWidth, resizable: false }), [sidebarWidth]);
  const metrics = useMemo(() => ({ minRowHeight }), [minRowHeight]);
  const labels = useMemo<GanttI18nConfig["labels"]>(() => ({
    today: t("gantt.today"), previous: t("gantt.previous"), next: t("gantt.next"),
    addEvent: t("gantt.addEvent"), addTask: t("gantt.addTask"), allDay: t("gantt.allDay"),
    loading: t("gantt.loading"), event: t("gantt.event"), resources: laneHeader ?? t("gantt.resources"),
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
  }), [t, laneHeader]);
  const i18n = useMemo(() => ({ labels }), [labels]);
  const renderNoResources = useCallback(() => t("gantt.empty"), [t]);
  const renderResourceLabel = useCallback<NonNullable<GanttProps["renderResourceLabel"]>>(({ resource }) => (
    <div className="min-w-0 flex-1">
      {renderRowContent ? renderRowContent(resource) : <span className="block truncate" title={resource.title}>{resource.title}</span>}
    </div>
  ), [renderRowContent]);
  const handleEventClick = useCallback<NonNullable<GanttProps["onEventClick"]>>(
    (occurrence) => onEventClick?.(occurrence.event), [onEventClick],
  );
  return (
    <Gantt
      {...props}
      defaultScale={props.defaultScale ?? (fitToEvents ? "quarter" : undefined)}
      range={range}
      weekStartsOn={1}
      onDateChange={handleDateChange}
      onScaleChange={handleScaleChange}
      treePanel={treePanel}
      metrics={metrics}
      rowAlign="center"
      infiniteScroll={!fitting}
      className={cn("h-full flex-1", className)}
      resources={resources}
      events={events}
      interactions={READ_ONLY}
      i18n={i18n}
      zoom={zoom}
      onZoomChange={setZoom}
      zoomControl={false}
      rowCheckboxes={onSelectedRowsChange !== undefined}
      selectedRows={selectedRows ? [...selectedRows] : undefined}
      onSelectedRowsChange={onSelectedRowsChange}
      scheduleMode="multiple"
      initialCenter={range?.start ?? "anchor"}
      renderNoResources={renderNoResources}
      renderResourceLabel={renderResourceLabel}
      onEventClick={onEventClick ? handleEventClick : undefined}
      onResourceClick={onResourceClick ? ({ resource }) => onResourceClick(resource) : undefined}
    >
      <GanttNav>
        <GanttNavToday />
        <GanttNavPrev />
        <GanttNavNext />
        <GanttTitle className="min-w-0 flex-1" />
        <GanttScaleSwitcher />
        <Button variant="ghost" size="iconSm" aria-label={t("gantt.zoomOut")} onClick={() => setZoom((value) => Math.max(0.5, +(value - 0.25).toFixed(2)))}><Glyph name="minus" size={12} /></Button>
        <Button variant="ghost" size="iconSm" aria-label={t("gantt.zoomIn")} onClick={() => setZoom((value) => Math.min(3, +(value + 0.25).toFixed(2)))}><Glyph name="plus" size={12} /></Button>
      </GanttNav>
      <GanttGrid />
    </Gantt>
  );
}
