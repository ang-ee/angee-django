import { keysetFeedRows } from "@angee/refine";
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
import { useTimelineT, type TimelineT } from "./i18n";
import { useTimelineFeed } from "./useTimelineFeed";

export const TIMELINE_TAB_ID = "timeline.rail";
export const TIMELINE_KINDS_CONTAINER = "timeline#kinds";

/**
 * The Timeline tab: the union read, paged by keyset cursor, drawn by the rail.
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
    if (selection?.focus === TIMELINE_FOCUS_NEEDS_ME) setScope(TIMELINE_SCOPE_NEEDS_ME);
  }, [selection?.focus]);
  const { setActiveTab } = useChatter();
  const recordHref = useResourceRecordHrefLookup();
  const navigate = useNavigate();
  const t = useTimelineT();
  const presentations = useContainer<TimelineKindContent>(TIMELINE_KINDS_CONTAINER);
  const presentationById = React.useMemo(() => new Map(presentations.map((child) => [child.id, child.content])), [presentations]);

  const kindFilter = scope === TIMELINE_SCOPE_ALL || scope === TIMELINE_SCOPE_NEEDS_ME ? null : [scope];
  const needsMe = scope === TIMELINE_SCOPE_NEEDS_ME;
  const feed = useTimelineFeed({ model, id, kinds: kindFilter, needsMe });
  const metadata = feed.data?.pages[0]?.metadata;
  const kinds = metadata?.kinds;
  const rows = React.useMemo(() => {
    // Keep the backend's complete ordering, including same-time tie breakers.
    const order = new Map(feed.data?.pages.flatMap((page) => page.rows).map((row, index) => [row.id, index]));
    return keysetFeedRows(feed.data, (left, right) => order.get(left.id)! - order.get(right.id)!);
  }, [feed.data]);
  const targets = React.useMemo(() => new Map(rows.map((entry) => [entry.key, entry])), [rows]);
  const entries = React.useMemo(() => rows.map((entry) => toLine(entry, presentationById, recordHref, t)),
    [rows, presentationById, recordHref, t]);
  const railKinds = React.useMemo<readonly TimelineRailKind[]>(() => (kinds ?? []).map((kind) => ({
    id: kind.id,
    count: kind.count ?? 0,
    // A kind no addon presents keeps a neutral face: its id is a Python label, never user text.
    ...(presentationById.get(kind.id) ?? { label: t("kind.unknown"), icon: "activity", tone: "muted" as const, verb: "", target: { page: true as const } }),
  })), [kinds, presentationById, t]);
  const needsMeCount = metadata?.needsMe ?? 0;

  const open = React.useCallback((line: TimelineRailEntry) => {
    const presentation = presentationById.get(line.kind);
    const target = targets.get(line.key);
    if (presentation && "tab" in presentation.target && target?.target_id) {
      setActiveTab(presentation.target.tab, { from: TIMELINE_TAB_ID, target: target.target_id, focus: presentation.target.focus });
      return;
    }
    if (line.href) void navigate({ href: line.href });
  }, [navigate, presentationById, setActiveTab, targets]);

  return (
    <TimelineRail
      entries={entries}
      kinds={railKinds}
      needsMe={needsMeCount}
      scope={scope}
      onScope={setScope}
      pending={feed.isFetching}
      error={feed.error?.message}
      onRetry={() => { void feed.refetch(); }}
      hasMore={feed.hasNextPage}
      onLoadOlder={() => { void feed.fetchNextPage(); }}
      onOpen={open}
    />
  );
}

function toLine(
  entry: TimelinePageEntry,
  presentations: ReadonlyMap<string, TimelineKindContent>,
  recordHref: (model: string, id: string) => string | undefined,
  t: TimelineT,
): TimelineRailEntry {
  const presentation = presentations.get(entry.kind);
  const href = presentation && "page" in presentation.target && entry.target_model && entry.target_id
    ? recordHref(entry.target_model, entry.target_id)
    : undefined;
  // What folds under the line, named by each child kind's own presentation.
  const folded = entry.children
    .map((child) => t("folded", { label: kindLabel(presentations.get(child.kind)?.label, t), count: child.count }))
    .join(" · ");
  return {
    key: entry.key, at: entry.at, kind: entry.kind, title: entry.title, actor: entry.actor,
    detail: entry.detail || undefined, needsMe: entry.needs_me, folded: folded || undefined, href,
  };
}

/** A kind's presented label as text; a kind without one, or with a node label, reads as activity. */
function kindLabel(label: React.ReactNode, t: TimelineT): string {
  return typeof label === "string" ? label : t("kind.unknown");
}
