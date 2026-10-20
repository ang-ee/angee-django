import {
  TIMELINE_SCOPE_ALL,
  TIMELINE_SCOPE_NEEDS_ME,
  TimelineRail,
  useChatter,
  type TimelineRailEntry,
  type TimelineRailKind,
} from "@angee/ui";
import { useContainer, useResourceRecordHrefLookup, type ChatterSelection, type ChatterViewContext, type TimelineKindContent } from "@angee/ui/runtime";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import type { TimelinePageEntry } from "./documents.console";
import { useTimelinePages } from "./useTimelinePages";

export const TIMELINE_TAB_ID = "timeline.rail";
export const TIMELINE_KINDS_CONTAINER = "timeline#kinds";

/**
 * The Timeline tab: the ledger read, paged by keyset cursor, drawn by the rail.
 * Each kind's presentation (label, glyph, verb, where it leads) is the
 * `timeline#kinds` child its owner declared; a line's verb opens the kind's
 * drill-in with the line's target, or navigates to the target's page.
 */
/** The focus the Needs you control opens the tab with. */
export const TIMELINE_FOCUS_NEEDS_ME = "needs-me";

export function RecordTimelinePane({ context, selection }: { context: ChatterViewContext; selection?: ChatterSelection }): React.ReactElement {
  const model = context.route?.modelLabel ?? context.route?.canonicalLabel ?? "";
  const id = context.view.kind === "record" ? context.view.sqid ?? "" : "";
  const [scope, setScope] = React.useState<string>(selection?.focus === TIMELINE_FOCUS_NEEDS_ME ? TIMELINE_SCOPE_NEEDS_ME : TIMELINE_SCOPE_ALL);
  React.useEffect(() => {
    if (selection?.focus === TIMELINE_FOCUS_NEEDS_ME) { setScope(TIMELINE_SCOPE_NEEDS_ME); setCursors([undefined]); }
  }, [selection?.focus]);
  const [cursors, setCursors] = React.useState<readonly (string | undefined)[]>([undefined]);
  const { setActiveTab } = useChatter();
  const recordHref = useResourceRecordHrefLookup();
  const navigate = useNavigate();
  const presentations = useContainer<TimelineKindContent>(TIMELINE_KINDS_CONTAINER);
  const presentationById = React.useMemo(() => new Map(presentations.map((child) => [child.id, child.content])), [presentations]);

  const kindFilter = scope === TIMELINE_SCOPE_ALL || scope === TIMELINE_SCOPE_NEEDS_ME ? null : [scope];
  const needsMe = scope === TIMELINE_SCOPE_NEEDS_ME;
  const pages = useTimelinePages({ model, id, kinds: kindFilter, needsMe }, cursors);
  const first = pages[0];
  const kinds = first?.data?.timeline.kinds;

  const entries = React.useMemo<readonly TimelineRailEntry[]>(() => {
    const seen = new Set<string>();
    const rows: TimelineRailEntry[] = [];
    for (const page of pages) {
      for (const entry of page?.data?.timeline.entries ?? []) {
        if (seen.has(entry.key)) continue;
        seen.add(entry.key);
        rows.push(toLine(entry, presentationById, recordHref));
      }
    }
    return rows;
  }, [pages, presentationById, recordHref]);
  const railKinds = React.useMemo<readonly TimelineRailKind[]>(() => (kinds ?? []).map((kind) => ({
    id: kind.id,
    count: kind.count ?? 0,
    ...(presentationById.get(kind.id) ?? { label: kind.id, icon: "activity", tone: "muted" as const, verb: "", target: { page: true as const } }),
  })), [kinds, presentationById]);
  const needsMeCount = first?.data?.timeline.needs_me ?? 0;
  const last = pages[pages.length - 1];
  const error = pages.find((page) => page?.error)?.error ?? null;

  const open = React.useCallback((line: TimelineRailEntry) => {
    const presentation = presentationById.get(line.kind);
    const target = targets.current.get(line.key);
    if (presentation && "tab" in presentation.target && target?.target_id) {
      setActiveTab(presentation.target.tab, { from: TIMELINE_TAB_ID, target: target.target_id, focus: presentation.target.focus });
      return;
    }
    if (line.href) void navigate({ href: line.href } as never);
  }, [navigate, presentationById, setActiveTab]);
  const targets = React.useRef(new Map<string, TimelinePageEntry>());
  React.useEffect(() => {
    targets.current = new Map(pages.flatMap((page) => (page?.data?.timeline.entries ?? []).map((entry) => [entry.key, entry] as const)));
  });

  return (
    <TimelineRail
      entries={entries}
      kinds={railKinds}
      needsMe={needsMeCount}
      scope={scope}
      onScope={(next) => { setScope(next); setCursors([undefined]); }}
      pending={pages.some((page) => page?.isLoading || page?.isFetching)}
      error={error ? error.message : null}
      onRetry={() => { for (const page of pages) void page?.refetch(); }}
      hasMore={Boolean(last?.data?.timeline.has_more)}
      onLoadOlder={() => {
        const before = last?.data?.timeline.next_before;
        if (before) setCursors((current) => [...current, before]);
      }}
      onOpen={open}
    />
  );
}

function toLine(
  entry: TimelinePageEntry,
  presentations: ReadonlyMap<string, TimelineKindContent>,
  recordHref: (model: string, id: string) => string | undefined,
): TimelineRailEntry {
  const presentation = presentations.get(entry.kind);
  const href = presentation && "page" in presentation.target && entry.target_model && entry.target_id
    ? recordHref(entry.target_model, entry.target_id)
    : undefined;
  // What folds under the line, named by each child kind's own presentation.
  const folded = entry.children
    .map((child) => `${child.count} ${labelOf(presentations.get(child.kind)?.label, child.kind, child.count)}`)
    .join(" · ");
  return {
    key: entry.key, at: entry.at, kind: entry.kind, title: entry.title, actor: entry.actor,
    detail: entry.detail || undefined, needsMe: entry.needs_me, folded: folded || undefined, href,
  };
}

function labelOf(label: React.ReactNode, kind: string, count: number): string {
  const text = typeof label === "string" ? label : kind;
  return count === 1 && text.endsWith("s") ? text.slice(0, -1).toLowerCase() : text.toLowerCase();
}
