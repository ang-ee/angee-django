import * as React from "react";
import { useNavigate } from "@tanstack/react-router";

import { useInAppNavigator } from "../../lib/in-app-link";
import { useUiT } from "../../i18n";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { Button } from "../../ui/button";
import type { PagerState } from "../../ui/pager";
import type { ResourceToolbarViewControls } from "../../toolbars";
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
import type { ResourceViewContextValue } from "../resource/resource-view-context";
import {
  DEFAULT_RESOURCE_VIEW_PAGE_SIZE,
  todayCalendarAnchor,
  type ResourceViewKind,
} from "../resource/resource-view-model";
import type { CalendarViewSpec } from "../resource/resource-view-types";
import { isCalendarSourceKey } from "../resource/model/search";
import { ResourceListFrame } from "../resource/ResourceListFrame";
import { useResourceSearch } from "../resource/search/use-resource-search";
import { useSearchCatalog } from "../resource/search/catalog";
import type { ResourceToolbarProps } from "../../toolbars";

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

/**
 * @kind surface
 */
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
  const navigateInApp = useInAppNavigator();
  const { mode, anchor, hiddenSources } = resourceView.state;
  const toggles = React.useMemo(() => calendarSourceToggles(calendar.sources), [calendar.sources]);
  const hidden = React.useMemo(() => new Set(hiddenSources), [hiddenSources]);
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
      if (occurrence.to) {
        if (navigateInApp) navigateInApp(occurrence.to);
        else void navigate({ href: occurrence.to });
      }
    },
    [navigate, navigateInApp],
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
      ...(toggles.length > 0 ? { sources: {
        options: toggles,
        visible: toggles.flatMap((toggle) => hidden.has(toggle.value) ? [] : [toggle.value]),
        onVisibleChange: (visible: readonly string[]) => resourceView.setHiddenSources(
          toggles.flatMap((toggle) => visible.includes(toggle.value) ? [] : [toggle.value])),
      } } : {}),
    }),
    [mode, anchor, t, toggles, hidden, resourceView.setMode, resourceView.setAnchor, resourceView.setHiddenSources],
  );

  const catalog = useSearchCatalog({ resourceView, columns: [], rows: [], modelMetadata: null, inferOptions: false, textFilterField: null });
  const search = useResourceSearch({ resourceView, catalog, groupingEnabled: false });
  const toolbar: ResourceToolbarProps = {
    search,
    onViewChange: availableViews.length > 1 ? resourceView.setView : undefined,
    onPageChange: resourceView.setPage, onPageSizeChange: resourceView.setPageSize,
    view: "calendar",
    pager: CALENDAR_PAGER,
    actions: toolbarActions,
    viewControls,
    availableViews,
    createLabel,
    onCreate,
  };

  return (
    <CalendarWindowSources sources={calendar.sources} hidden={hidden} range={fetchWindow} enabled={settled}>
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
 * The toolbar toggles for a page's labelled sources, after checking every key:
 * keys are unique source ids a URL can carry, and a label needs a key. One
 * source alone gets no toggle; hiding it would leave an empty grid.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function calendarSourceToggles(
  sources: readonly AnyCalendarWindowSource[],
): readonly { value: string; label: React.ReactNode }[] {
  const keys = new Set<string>();
  const toggles: { value: string; label: React.ReactNode }[] = [];
  for (const source of sources) {
    if (source.key === undefined) {
      if (source.label !== undefined) throw new Error("A calendar source with a label needs a key.");
      continue;
    }
    if (!isCalendarSourceKey(source.key)) {
      throw new Error(`Calendar source key "${source.key}" may hold only letters, digits, "_", "-" and ".".`);
    }
    if (keys.has(source.key)) throw new Error(`Two calendar sources share the key "${source.key}".`);
    keys.add(source.key);
    if (source.label !== undefined) toggles.push({ value: source.key, label: source.label });
  }
  return sources.length > 1 ? toggles : [];
}

/**
 * Fetch a page-declared set of occurrence sources over one window and hand the
 * merged result to `children`. The sources are a stable, page-declared set, so a
 * fetcher-per-source render chain (never a hook in a loop) keeps the hook order
 * fixed while a single generic can't name the heterogeneous documents.
 */
function CalendarWindowSources({
  sources,
  hidden,
  range,
  enabled,
  children,
}: {
  sources: readonly AnyCalendarWindowSource[];
  hidden: ReadonlySet<string>;
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
      hidden={hidden}
      range={range}
      enabled={enabled}
      render={children}
    />
  );
}

function CalendarWindowSourceNode({
  source,
  rest,
  hidden,
  range,
  enabled,
  render,
}: {
  source: AnyCalendarWindowSource;
  rest: readonly AnyCalendarWindowSource[];
  hidden: ReadonlySet<string>;
  range: CalendarWindow;
  enabled: boolean;
  render: (fetched: CalendarWindowFetch) => React.ReactElement;
}): React.ReactElement {
  // A hidden source pauses its fetch and leaves the grid, keeping the hook order.
  const shown = source.key === undefined || !hidden.has(source.key);
  // The surface's settled gate composes with the source's own opt-in; memoised so
  // the fetch key (and its occurrence projection) stay referentially stable.
  const fetchSource = React.useMemo<AnyCalendarWindowSource>(
    () => ({ ...source, enabled: enabled && shown && (source.enabled ?? true) }),
    [source, enabled, shown],
  );
  const fetched = useCalendarWindow(fetchSource, range);
  const head = shown ? fetched : EMPTY_CALENDAR_FETCH;
  return (
    <CalendarWindowSources sources={rest} hidden={hidden} range={range} enabled={enabled}>
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
