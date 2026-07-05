import {
  useAuthoredQuery,
  type AuthoredQueryOptions,
  type TypedDocumentNode,
} from "@angee/refine";

import type { CalendarWindow, Occurrence } from "./CalendarView";

/** The window bounds a server-side occurrence query takes. */
export interface CalendarWindowBounds {
  /** ISO-8601 UTC; the resolver treats it inclusive (§3.2). */
  window_start: string;
  /** ISO-8601 UTC; the resolver treats it exclusive (§3.2). */
  window_end: string;
}

/**
 * The §3.2 window contract encoded once: `start` inclusive, `end` exclusive,
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
 * result. The query and its shape live downstream (`arp.calendar`), so the
 * source declares both here rather than the hook probing the result.
 */
export interface CalendarWindowSource<TData, TVariables> {
  /** The authored occurrence query (`event_occurrences` / `activity_agenda`) returning the §3.3 wire shape. */
  document: TypedDocumentNode<TData, TVariables>;
  /** Build the query variables for a window — the source owns its argument names (compose `calendarWindowBounds`). */
  variables: (window: CalendarWindow) => TVariables;
  /** Read the occurrence array out of the result (the result field is the source's). */
  select: (data: TData | undefined) => readonly Occurrence[] | null | undefined;
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
 * The F-cal window adapter: fetch a source's occurrences for a window and hand
 * them to {@link CalendarView} in the §3.3 wire shape, refetching when the
 * window changes. Transport is the `@angee/refine` authored-query owner
 * ({@link useAuthoredQuery}) — the window bounds ride the query variables, so a
 * new window is a new key and the owner refetches; the caller stays a thin
 * composer and never re-authors the mapping per page.
 */
export function useCalendarWindow<TData, TVariables>(
  source: CalendarWindowSource<TData, TVariables>,
  window: CalendarWindow,
): UseCalendarWindowResult {
  const options: AuthoredQueryOptions = {
    models: source.models,
    dataProviderName: source.dataProviderName,
    enabled: source.enabled,
  };
  // The authored-query owner erases variables to `any` on its document type
  // (`AuthoredDocument`), so instantiate it the same way: the source already
  // typed `document`↔`variables` together, and the transport only needs the run.
  const query = useAuthoredQuery<TypedDocumentNode<TData, any>>(
    source.document,
    source.variables(window),
    options,
  );
  return {
    occurrences: source.select(query.data) ?? NO_OCCURRENCES,
    fetching: query.fetching,
    error: query.error,
    refetch: query.refetch,
  };
}
