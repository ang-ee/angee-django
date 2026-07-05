import { useMemo } from "react";
import {
  useAuthoredQuery,
  type AuthoredDocument,
  type AuthoredQueryOptions,
  type AuthoredVariables,
  type DocumentData,
} from "@angee/refine";

import type { CalendarWindow, Occurrence } from "./CalendarView";

/** The window bounds a server-side occurrence query takes. */
export interface CalendarWindowBounds {
  /** ISO-8601 UTC; the resolver treats it inclusive. */
  window_start: string;
  /** ISO-8601 UTC; the resolver treats it exclusive. */
  window_end: string;
}

/**
 * The window contract encoded once: `start` inclusive, `end` exclusive,
 * ISO-8601. A source's `variables` builder spreads it so the argument names stay
 * with the query while the bounds conversion lives here.
 */
export function calendarWindowBounds(window: CalendarWindow): CalendarWindowBounds {
  return {
    window_start: window.start.toISOString(),
    window_end: window.end.toISOString(),
  };
}

/**
 * One occurrence source for {@link useCalendarWindow}: the authored query, how a
 * window maps to its variables, and how to read the occurrence array out of its
 * result. The query and its shape live downstream, so the source declares both
 * here rather than the hook probing the result.
 */
export interface CalendarWindowSource<TDocument extends AuthoredDocument> {
  /** The authored occurrence query returning the occurrence wire shape. */
  document: TDocument;
  /** Build the query variables for a window — the source owns its argument names (compose `calendarWindowBounds`). */
  variables: (window: CalendarWindow) => AuthoredVariables<TDocument>;
  /** Read the occurrence array out of the result (the result field is the source's). */
  select: (
    data: DocumentData<TDocument> | undefined,
  ) => readonly Occurrence[] | null | undefined;
  /** Model labels whose local writes / live changes refetch this window. */
  models?: readonly string[];
  /** Refine data provider (schema bucket); defaults to the active layout schema. */
  dataProviderName?: string;
  /** Skip the fetch while false (e.g. before a range is known). */
  enabled?: boolean;
}

export interface UseCalendarWindowResult {
  occurrences: readonly Occurrence[];
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

const NO_OCCURRENCES: readonly Occurrence[] = [];

/**
 * The window adapter: fetch a source's occurrences for a window and hand them to
 * {@link CalendarView} in the occurrence wire shape, refetching when the window
 * changes. Transport is the `@angee/refine` authored-query owner
 * ({@link useAuthoredQuery}) — the window bounds ride the query variables, so a
 * new window is a new key and the owner refetches; the caller stays a thin
 * composer and never re-authors the mapping per page.
 */
export function useCalendarWindow<TDocument extends AuthoredDocument>(
  source: CalendarWindowSource<TDocument>,
  window: CalendarWindow,
): UseCalendarWindowResult {
  const options: AuthoredQueryOptions = {
    models: source.models,
    dataProviderName: source.dataProviderName,
    enabled: source.enabled,
  };
  const query = useAuthoredQuery(source.document, source.variables(window), options);
  // FullCalendar compares its events option element-wise by reference, so a new
  // selected array each render re-parses the source. `query.data` is stable
  // across renders (react-query structural sharing), so memoize the projection.
  const occurrences = useMemo(
    () => source.select(query.data) ?? NO_OCCURRENCES,
    [source, query.data],
  );
  return {
    occurrences,
    fetching: query.fetching,
    error: query.error,
    refetch: query.refetch,
  };
}
