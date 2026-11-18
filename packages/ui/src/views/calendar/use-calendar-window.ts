import { useMemo, type ReactNode } from "react";
import {
  useAuthoredQueryBatch,
  type AuthoredDocument,
  type AuthoredQueryBatchScope,
  type AuthoredVariables,
  type DocumentData,
} from "@angee/refine";
import { canonicalResourceModelLabels, useSchemaFieldMetadata } from "@angee/metadata";

import type { LabelKey } from "../../lib/labels";

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
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
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
 * here rather than the hook probing the result. A source's `select` may also map
 * its rows onto occurrences carrying a route `to` (see {@link Occurrence.to}), so
 * marker navigation is source-declared rather than page-woven.
 */
export interface CalendarWindowSource<TDocument extends AuthoredDocument> {
  /**
   * The source's id within its page (letters, digits, `_`, `-`, `.`), named by
   * `?hiddenSources=` and shipped views. A source with a `key` and a `label`
   * gets a toolbar toggle that hides or shows its occurrences.
   */
  key?: string;
  /** The toolbar toggle's label, rendered content or an addon message key; requires `key`. */
  label?: ReactNode | LabelKey;
  /** The authored occurrence query returning the occurrence wire shape. */
  document: TDocument;
  /** Build the query variables for a window — the source owns its argument names (compose `calendarWindowBounds`). */
  variables: (window: CalendarWindow) => AuthoredVariables<TDocument>;
  /** Read the occurrence array out of the result (the result field is the source's). */
  select: (
    data: DocumentData<TDocument> | undefined,
  ) => readonly Occurrence[] | null | undefined;
  /** Model spellings canonicalized at the metadata edge for live/refetch interest. */
  models?: readonly string[];
  /** Refine data provider (schema bucket); defaults to the active layout schema. */
  dataProviderName?: string;
  /** Skip the fetch while false (e.g. before a range is known). */
  enabled?: boolean;
}

/**
 * A calendar source with its document type erased. A calendar surface fetches a
 * stable, page-declared *set* of sources whose element documents differ, which no
 * single generic can name — each source stays internally typed where it is
 * authored (compose {@link calendarWindowSource} to widen one into the set).
 */
export type AnyCalendarWindowSource = CalendarWindowSource<AuthoredDocument>;

/**
 * Widen a typed source into the erased set type, keeping its `variables`/`select`
 * checked against `TDocument` at the authoring site. The erasure boundary is here,
 * once, so a page never hand-casts.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function calendarWindowSource<TDocument extends AuthoredDocument>(
  source: CalendarWindowSource<TDocument>,
): AnyCalendarWindowSource {
  return source as AnyCalendarWindowSource;
}

/** The merged fetch across a page's occurrence sources for one window. */
export interface UseCalendarWindowResult {
  occurrences: readonly Occurrence[];
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

export interface CalendarWindowOptions {
  /** Keys of sources whose occurrences stay off the grid: they leave the batch, so nothing fetches for them. */
  hidden?: ReadonlySet<string>;
  /** Skip every fetch while false (e.g. before the grid reports its window). */
  enabled?: boolean;
}

const NO_OCCURRENCES: readonly Occurrence[] = [];
const dataIdentities = new WeakMap<object, number>();
let nextDataIdentity = 0;

/** A key naming each result's identity: query data is structurally shared, so unchanged data keeps its key. */
function dataIdentityKey(results: readonly unknown[]): string {
  return results.map((data) => {
    if (typeof data !== "object" || data === null) return String(data);
    let identity = dataIdentities.get(data);
    if (identity === undefined) {
      identity = ++nextDataIdentity;
      dataIdentities.set(data, identity);
    }
    return `#${identity}`;
  }).join(",");
}

/**
 * The window adapter: fetch a page's occurrence sources for a window and hand
 * {@link CalendarView} their occurrences in the wire shape, refetching when the
 * window changes. Transport is one `@angee/refine` batch read
 * ({@link useAuthoredQueryBatch}) over every shown source, so a source set that
 * grows or shrinks (a calendar list loading, a toggle) changes no hook order and
 * never remounts the grid; sources sharing a document and variables share one
 * request. The window bounds ride each source's variables, so a new window is a
 * new key and the owner refetches. A hidden source, and one that opts out
 * (`enabled: false`), leave the batch. The sources share one data provider.
 * @owner CalendarView
 */
export function useCalendarWindow(
  sources: readonly AnyCalendarWindowSource[],
  window: CalendarWindow,
  { hidden, enabled = true }: CalendarWindowOptions = {},
): UseCalendarWindowResult {
  const metadata = useSchemaFieldMetadata();
  const shown = useMemo(
    () => sources.filter((source) => (source.key === undefined || !hidden?.has(source.key)) && (source.enabled ?? true)),
    [sources, hidden],
  );
  const providers = new Set(shown.map((source) => source.dataProviderName));
  if (providers.size > 1) throw new Error("A calendar's occurrence sources must share one data provider.");
  const scopes = useMemo<readonly AuthoredQueryBatchScope<AuthoredDocument>[]>(
    () => shown.map((source, index) => ({
      // Source keys hold only letters, digits, `_`, `-` and `.`; an unkeyed one is labelled by position.
      key: source.key ?? `#${index}`,
      document: source.document,
      variables: source.variables(window),
      models: canonicalResourceModelLabels(metadata, source.models),
    })),
    [shown, window, metadata],
  );
  const results = useAuthoredQueryBatch(scopes, { enabled, dataProviderName: shown[0]?.dataProviderName });
  const answered = scopes.map((scope) => results.get(scope.key));
  const data = answered.map((result) => result?.data);
  const dataKey = dataIdentityKey(data);
  // FullCalendar compares its events option element-wise by reference, so the merged
  // array changes only when a source or one of its results does.
  const occurrences = useMemo(
    () => {
      const parts = shown.map((source, index) => source.select(data[index] as never) ?? NO_OCCURRENCES);
      if (parts.length === 0) return NO_OCCURRENCES;
      return parts.length === 1 ? parts[0]! : parts.flat();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shown, dataKey],
  );
  return {
    occurrences,
    fetching: answered.some((result) => result?.isFetching === true),
    error: answered.find((result) => result?.error)?.error ?? null,
    refetch: () => {
      for (const result of answered) void result?.refetch();
    },
  };
}
