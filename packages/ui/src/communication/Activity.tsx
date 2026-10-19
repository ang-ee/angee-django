import * as React from "react";

import { EmptyState } from "../fragments/EmptyState";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import {
  modelChain,
  useContainer,
  type ChatterViewContext,
  type ComposedContainerChild,
} from "../runtime";
import { Button } from "../ui/button";
import { MessageFeed, MessageRow } from "./message";

export const ACTIVITY_TAB_ID = "activity";
export const ACTIVITY_PAGE_SIZE = 25;

export const ACTIVITY_KINDS = [
  "comment",
  "change",
  "run",
  "decision",
  "conversation",
  "source-event",
] as const;

export type ActivityKind = (typeof ACTIVITY_KINDS)[number];

/** The canonical record identity handed to every contributed activity source. */
export interface ActivityRecord {
  model: string;
  id: string;
  /** Edge metadata lets a source decline records its owner cannot attach to. */
  recordEdges: readonly string[];
  history: boolean;
}

/** One entry in the merged record stream. Sources keep rendering ownership. */
export interface ActivityEntry {
  at: Date | string;
  kind: ActivityKind;
  actor: React.ReactNode;
  title: React.ReactNode;
  body: React.ReactNode;
  reference: React.ReactNode;
}

/**
 * The shared newest-first window. A source may publish its existing composer
 * without adding another tab or teaching Activity a product-specific mutation.
 */
export interface ActivityWindow {
  limit: number;
  publishComposer: (composer: React.ReactNode | null) => void;
}

/** Exact content of one `record#activity` container child. */
export interface ActivityEntrySource {
  useEntries: (
    record: ActivityRecord,
    window: ActivityWindow,
  ) => readonly ActivityEntry[];
  kinds: readonly ActivityKind[];
}

declare module "../runtime/containers" {
  interface ContainerKinds {
    /** Model-scoped entry sources merged by the framework Activity tab. */
    activity: ActivityEntrySource;
  }
}

export interface ActivityProps {
  context: ChatterViewContext;
  className?: string;
}

/** The framework-owned record stream: merge, filter and paging live here once. */
export function Activity({ context, className }: ActivityProps): React.ReactElement {
  const t = useUiT();
  const model = context.route?.canonicalLabel ?? context.route?.modelLabel ?? "";
  const id = context.view.kind === "record" ? context.view.sqid ?? "" : "";
  const models = React.useMemo(
    () => modelChain(context.route?.canonicalLabel, context.route?.modelLabel),
    [context.route?.canonicalLabel, context.route?.modelLabel],
  );
  const sources = useContainer<ActivityEntrySource>("record#activity", { models });
  const record = React.useMemo<ActivityRecord>(() => ({
    model,
    id,
    recordEdges: context.route?.recordEdges ?? [],
    history: context.route?.history === true,
  }), [context.route?.history, context.route?.recordEdges, id, model]);
  const [limit, setLimit] = React.useState(ACTIVITY_PAGE_SIZE);
  const [kind, setKind] = React.useState<ActivityKind | "all">("all");
  const [batches, setBatches] = React.useState<ReadonlyMap<string, readonly ActivityEntry[]>>(new Map());
  const [composers, setComposers] = React.useState<ReadonlyMap<string, React.ReactNode>>(new Map());

  const publishEntries = React.useCallback((source: string, entries: readonly ActivityEntry[] | null) => {
    setBatches((current) => {
      if (entries !== null && current.get(source) === entries) return current;
      if (entries === null && !current.has(source)) return current;
      const next = new Map(current);
      if (entries === null) next.delete(source);
      else next.set(source, entries);
      return next;
    });
  }, []);
  const publishComposer = React.useCallback((source: string, composer: React.ReactNode | null) => {
    setComposers((current) => {
      if (composer !== null && current.get(source) === composer) return current;
      if (composer === null && !current.has(source)) return current;
      const next = new Map(current);
      if (composer === null) next.delete(source);
      else next.set(source, composer);
      return next;
    });
  }, []);

  const availableKinds = React.useMemo(() => new Set(sources.flatMap((source) => source.content.kinds)), [sources]);
  React.useEffect(() => {
    if (kind !== "all" && !availableKinds.has(kind)) setKind("all");
  }, [availableKinds, kind]);
  const entries = React.useMemo(() => [...batches.entries()].flatMap(([source, batch]) =>
    batch.map((entry, index) => ({ entry, key: `${source}:${entry.kind}:${String(entry.at)}:${index}` })))
    .sort((left, right) => activityTime(right.entry.at) - activityTime(left.entry.at)), [batches]);
  // Filter before paging: the kind filter selects among everything the sources published, so a
  // kind that only appears further back is still shown and the empty state means "none", not
  // "none in the newest page".
  const matching = kind === "all" ? entries : entries.filter(({ entry }) => entry.kind === kind);
  const filtered = matching.slice(0, limit);
  const ready = sources.every((source) => batches.has(source.id));
  const canLoadOlder = matching.length > limit || [...batches.values()].some((batch) => batch.length >= limit);

  if (!model || !id) {
    return <EmptyState icon="activity" title={t("activity.empty.title")} description={t("activity.empty.description")} className="min-h-48 p-4" />;
  }

  return <div className={cn("flex min-h-0 flex-1 flex-col", className)}>
    {sources.map((source) => <ActivitySourceProbe key={source.id} source={source} record={record} limit={limit}
      onEntries={publishEntries} onComposer={publishComposer} />)}
    <div className="flex min-w-0 flex-wrap gap-1 border-b border-border-subtle p-3" role="group" aria-label={t("activity.filter.label")}>
      <ActivityFilter active={kind === "all"} onClick={() => setKind("all")}>{t("activity.filter.all")}</ActivityFilter>
      {ACTIVITY_KINDS.filter((candidate) => availableKinds.has(candidate)).map((candidate) =>
        <ActivityFilter key={candidate} active={kind === candidate} onClick={() => setKind(candidate)}>
          {activityKindLabel(t, candidate)}
        </ActivityFilter>)}
    </div>
    <div className="min-h-0 flex-1 overflow-y-auto p-3">
      {!ready ? <LoadingPanel message={t("activity.loading")} density="inline" />
        : filtered.length === 0 ? <EmptyState icon="activity" title={t("activity.empty.title")}
          description={t("activity.empty.description")} className="min-h-48 p-4" />
          : <MessageFeed label={t("activity.title")} className="gap-5">
            {filtered.map(({ entry, key }) => <MessageRow key={key} author={entry.actor} timestamp={entry.at}
              channel={<span className="text-11 font-medium uppercase tracking-wide text-fg-muted">{activityKindLabel(t, entry.kind)}</span>}
              meta={entry.reference}>
              <span className="font-medium">{entry.title}</span>
              {entry.body ? <div className="mt-1">{entry.body}</div> : null}
            </MessageRow>)}
          </MessageFeed>}
      {ready && canLoadOlder ? <div className="mt-4 flex justify-center"><Button type="button" variant="ghost" size="sm"
        onClick={() => setLimit((current) => current + ACTIVITY_PAGE_SIZE)}>{t("activity.older")}</Button></div> : null}
    </div>
    {[...composers.entries()].map(([source, composer]) => <div key={source}
      className="min-w-0 shrink-0 overflow-hidden border-t border-border-subtle p-3">{composer}</div>)}
  </div>;
}

const ActivitySourceProbe = React.memo(function ActivitySourceProbe({ source, record, limit, onEntries, onComposer }: {
  source: ComposedContainerChild<ActivityEntrySource>;
  record: ActivityRecord;
  limit: number;
  onEntries: (source: string, entries: readonly ActivityEntry[] | null) => void;
  onComposer: (source: string, composer: React.ReactNode | null) => void;
}): null {
  const publishComposer = React.useCallback((composer: React.ReactNode | null) => onComposer(source.id, composer), [onComposer, source.id]);
  const window = React.useMemo<ActivityWindow>(() => ({ limit, publishComposer }), [limit, publishComposer]);
  const entries = source.content.useEntries(record, window);
  React.useEffect(() => {
    onEntries(source.id, entries);
    return () => onEntries(source.id, null);
  }, [entries, onEntries, source.id]);
  React.useEffect(() => () => onComposer(source.id, null), [onComposer, source.id]);
  return null;
});

function ActivityFilter({ active, onClick, children }: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}): React.ReactElement {
  return <Button type="button" size="sm" variant={active ? "secondary" : "ghost"} aria-pressed={active} onClick={onClick}>{children}</Button>;
}

function activityTime(value: Date | string): number {
  const time = value instanceof Date ? value.getTime() : Date.parse(value);
  return Number.isFinite(time) ? time : 0;
}

function activityKindLabel(t: ReturnType<typeof useUiT>, kind: ActivityKind): string {
  switch (kind) {
    case "comment": return t("activity.kind.comment");
    case "change": return t("activity.kind.change");
    case "run": return t("activity.kind.run");
    case "decision": return t("activity.kind.decision");
    case "conversation": return t("activity.kind.conversation");
    case "source-event": return t("activity.kind.sourceEvent");
  }
}
