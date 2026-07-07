import * as React from "react";
import { useNavigate } from "@tanstack/react-router";

import { useUiT } from "../i18n";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { Button } from "../ui/button";
import type { PagerState } from "../ui/pager";
import type { ResourceToolbarViewControls } from "../toolbars";
import { CalendarView, type CalendarWindow, type Occurrence } from "./CalendarView";
import {
  useCalendarWindow,
  type AnyCalendarWindowSource,
  type UseCalendarWindowResult,
} from "./use-calendar-window";
import {
  calendarAnchorToDate,
  calendarModeOptions,
  calendarPeriodTitle,
  shiftCalendarAnchor,
} from "./calendar-view-controls";
import type { ResourceViewContextValue } from "./resource-view-context";
import {
  DEFAULT_RESOURCE_VIEW_PAGE_SIZE,
  todayCalendarAnchor,
  type ResourceViewKind,
} from "./resource-view-model";
import type { CalendarViewSpec } from "./resource-view-types";
import { ResourceListFrame } from "./ResourceListFrame";
import { useResourceToolbarProps } from "./resource-toolbar-props";

// The windowed-collection surface at the `ListView` seam — a component boundary
// beside the client/grouped/server bodies, so the calendar's window-keyed fetch
// never reorders hooks with the list's `useList` path. It drives
// `useCalendarWindow` over the page-declared sources, owns settled-window fetch
// gating and loading/error/retry, contributes the typed view controls, and
// renders `CalendarView`. It never calls `useList`.

/** The pager is inapplicable under the calendar kind (the toolbar hides it); a
 * zero pager satisfies the shared toolbar contract. */
const CALENDAR_PAGER: PagerState = {
  total: 0,
  page: 1,
  pageSize: DEFAULT_RESOURCE_VIEW_PAGE_SIZE,
};

export interface CalendarCollectionSurfaceProps {
  resource: string;
  resourceView: ResourceViewContextValue;
  calendar: CalendarViewSpec;
  availableViews: readonly ResourceViewKind[];
  createLabel?: React.ReactNode;
  onCreate?: () => void;
  toolbarActions?: React.ReactNode;
  className?: string;
}

export function CalendarCollectionSurface({
  resourceView,
  calendar,
  availableViews,
  createLabel,
  onCreate,
  toolbarActions,
  className,
}: CalendarCollectionSurfaceProps): React.ReactElement {
  const t = useUiT();
  const navigate = useNavigate();
  const { mode, anchor } = resourceView.state;
  const anchorDate = React.useMemo(() => calendarAnchorToDate(anchor), [anchor]);
  // `CalendarView` positions the grid from `range.start`; the fetch window is the
  // padded window the grid reports back (`onRangeChange`), so the two are distinct.
  const positionRange = React.useMemo<CalendarWindow>(
    () => ({ start: anchorDate, end: anchorDate }),
    [anchorDate],
  );
  const [fetchWindow, setFetchWindow] = React.useState<CalendarWindow>(positionRange);
  // Gate the fetch until the grid reports its real padded window, so the degenerate
  // seed window (start === end) never fires a throwaway request the echo replaces.
  const [settled, setSettled] = React.useState(false);
  const onRangeChange = React.useCallback((next: CalendarWindow) => {
    setFetchWindow(next);
    setSettled(true);
  }, []);

  const handleEventClick = React.useCallback(
    (occurrence: Occurrence) => {
      // A marker with a source-declared route navigates; one without is inert.
      if (occurrence.to) void navigate({ to: occurrence.to });
    },
    [navigate],
  );

  const viewControls = React.useMemo<ResourceToolbarViewControls>(
    () => ({
      mode,
      modeOptions: calendarModeOptions(t),
      onModeChange: resourceView.setMode,
      title: calendarPeriodTitle(mode, anchor, t),
      onPrev: () => resourceView.setAnchor(shiftCalendarAnchor(mode, anchor, -1)),
      onToday: () => resourceView.setAnchor(todayCalendarAnchor()),
      onNext: () => resourceView.setAnchor(shiftCalendarAnchor(mode, anchor, 1)),
    }),
    [mode, anchor, t, resourceView.setMode, resourceView.setAnchor],
  );

  const toolbar = useResourceToolbarProps({
    resourceView,
    view: "calendar",
    pager: CALENDAR_PAGER,
    actions: toolbarActions,
    viewControls,
    availableViews,
    createLabel,
    onCreate,
  });

  return (
    <CalendarWindowSources sources={calendar.sources} range={fetchWindow} enabled={settled}>
      {(fetched) => (
        <ResourceListFrame
          className={className}
          toolbar={toolbar}
          loadingFooter={fetched.fetching && !fetched.error}
        >
          <ErrorBanner
            description={fetched.error ? t("calendar.loadFailed") : null}
            actions={
              fetched.error ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={fetched.refetch}
                >
                  {t("calendar.retry")}
                </Button>
              ) : undefined
            }
          />
          <CalendarView
            occurrences={fetched.occurrences}
            view={mode}
            range={positionRange}
            onRangeChange={onRangeChange}
            onEventDrop={calendar.onReschedule}
            onSelectRange={calendar.onSelectRange}
            onEventClick={handleEventClick}
          />
        </ResourceListFrame>
      )}
    </CalendarWindowSources>
  );
}

/** The merged fetch across a page's occurrence sources. */
export interface CalendarWindowFetch {
  occurrences: readonly Occurrence[];
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

const EMPTY_CALENDAR_FETCH: CalendarWindowFetch = {
  occurrences: [],
  fetching: false,
  error: null,
  refetch: () => undefined,
};

/**
 * Fetch a page-declared set of occurrence sources over one window and hand the
 * merged result to `children`. The sources are a stable, page-declared set, so a
 * fetcher-per-source render chain (never a hook in a loop) keeps the hook order
 * fixed while a single generic can't name the heterogeneous documents.
 */
function CalendarWindowSources({
  sources,
  range,
  enabled,
  children,
}: {
  sources: readonly AnyCalendarWindowSource[];
  range: CalendarWindow;
  enabled: boolean;
  children: (fetched: CalendarWindowFetch) => React.ReactElement;
}): React.ReactElement {
  const [source, ...rest] = sources;
  if (!source) return children(EMPTY_CALENDAR_FETCH);
  return (
    <CalendarWindowSourceNode
      source={source}
      rest={rest}
      range={range}
      enabled={enabled}
      render={children}
    />
  );
}

function CalendarWindowSourceNode({
  source,
  rest,
  range,
  enabled,
  render,
}: {
  source: AnyCalendarWindowSource;
  rest: readonly AnyCalendarWindowSource[];
  range: CalendarWindow;
  enabled: boolean;
  render: (fetched: CalendarWindowFetch) => React.ReactElement;
}): React.ReactElement {
  // The surface's settled gate composes with the source's own opt-in; memoised so
  // the fetch key (and its occurrence projection) stay referentially stable.
  const fetchSource = React.useMemo<AnyCalendarWindowSource>(
    () => ({ ...source, enabled: enabled && (source.enabled ?? true) }),
    [source, enabled],
  );
  const head = useCalendarWindow(fetchSource, range);
  return (
    <CalendarWindowSources sources={rest} range={range} enabled={enabled}>
      {(tail) => <CalendarWindowMerge head={head} tail={tail} render={render} />}
    </CalendarWindowSources>
  );
}

function CalendarWindowMerge({
  head,
  tail,
  render,
}: {
  head: UseCalendarWindowResult;
  tail: CalendarWindowFetch;
  render: (fetched: CalendarWindowFetch) => React.ReactElement;
}): React.ReactElement {
  const occurrences = React.useMemo(
    () =>
      tail.occurrences.length === 0
        ? head.occurrences
        : [...head.occurrences, ...tail.occurrences],
    [head.occurrences, tail.occurrences],
  );
  const merged = React.useMemo<CalendarWindowFetch>(
    () => ({
      occurrences,
      fetching: head.fetching || tail.fetching,
      error: head.error ?? tail.error,
      refetch: () => {
        head.refetch();
        tail.refetch();
      },
    }),
    [occurrences, head.fetching, tail.fetching, head.error, tail.error, head.refetch, tail.refetch],
  );
  return render(merged);
}
