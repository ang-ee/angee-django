import * as React from "react";

import { EmptyState } from "../fragments/EmptyState";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import { Chip } from "../ui/chip";
import { StatusIcon, type StatusIconTone } from "../ui/status-icon";

/**
 * The record timeline rail: one line per event, newest first, grouped by day.
 * A line is `title · detail` over `time · actor · verb`; the whole line is the
 * verb. Events that happened inside a run nest under that run's line, folded.
 * The rail never renders a body: bodies stay with their owners. Schema-independent:
 * the owning addon feeds it entries.
 */

export interface TimelineRailEntry {
  id: string;
  /** ISO datetime. */
  at: string;
  kind: string;
  title: string;
  actor: string;
  detail: string;
  needsMe: boolean;
  /** The run (its entry id) this event happened inside, when any. */
  groupId?: string | null;
}

export interface TimelineRailKind {
  id: string;
  label: string;
  icon: string;
  tone: StatusIconTone;
  /** The one verb a line of this kind carries. */
  verb: string;
}

export interface TimelineRailProps {
  entries: readonly TimelineRailEntry[];
  /** The kinds this record can carry, in order; the scope chips. */
  kinds: readonly TimelineRailKind[];
  counts: Readonly<Record<string, number>>;
  scope: string;
  onScope: (kind: string) => void;
  pending?: boolean;
  error?: string | null;
  hasMore?: boolean;
  onLoadOlder?: () => void;
  onOpen: (entry: TimelineRailEntry) => void;
  className?: string;
}

export const TIMELINE_SCOPE_ALL = "all";
export const TIMELINE_SCOPE_NEEDS_ME = "needs-me";
const RUN_KIND = "run";

const FALLBACK_KIND: Omit<TimelineRailKind, "id"> = { label: "", icon: "activity", tone: "muted", verb: "Open" };

function dayLabel(iso: string): string {
  const date = new Date(iso);
  const today = new Date();
  const start = (value: Date) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const days = Math.round((start(today) - start(date)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: days > 300 ? "numeric" : undefined });
}

function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function summarize(children: readonly TimelineRailEntry[], meta: Map<string, TimelineRailKind>): string {
  const counts = new Map<string, number>();
  for (const child of children) counts.set(child.kind, (counts.get(child.kind) ?? 0) + 1);
  return [...counts].map(([kind, count]) => `${count} ${(meta.get(kind)?.label ?? kind).toLowerCase()}`).join(" · ");
}

interface LineProps {
  entry: TimelineRailEntry;
  kind: TimelineRailKind;
  onOpen: (entry: TimelineRailEntry) => void;
  nested?: boolean;
  summary?: string;
  folded?: boolean;
  onToggle?: () => void;
}

function Line({ entry, kind, onOpen, nested = false, summary, folded, onToggle }: LineProps): React.ReactElement {
  return (
    <div className={cn("group/line relative -ml-1 rounded-6 py-1 pl-8 pr-1 motion-safe:transition-colors hover:bg-canvas/70", nested && "py-0.5")}>
      <span className="absolute top-1.5 grid size-5 place-items-center rounded-full bg-sheet" style={{ left: "-0.4375rem" }}>
        <StatusIcon tone={kind.tone} icon={kind.icon} size="sm" />
      </span>
      <button type="button" onClick={() => onOpen(entry)} aria-label={`${kind.verb}: ${entry.title}`} className="block w-full text-left outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-4">
        <div className={cn("leading-5", nested ? "text-12" : "text-13")}>
          <span className="font-medium">{entry.title}</span>
          {entry.detail ? <span className="text-fg-muted"> · {entry.detail}</span> : null}
        </div>
        <div className="flex flex-wrap items-center gap-x-1.5 text-11 leading-4 text-fg-muted">
          <span>{timeLabel(entry.at)}</span>
          {entry.actor ? <span>· {entry.actor}</span> : null}
          {entry.needsMe ? <Chip tone="warning" variant="soft">needs you</Chip> : null}
          <span className="text-link opacity-70 motion-safe:transition-opacity group-hover/line:opacity-100">{kind.verb} ›</span>
        </div>
      </button>
      {summary && onToggle ? (
        <button type="button" onClick={onToggle} aria-expanded={!folded} className="mt-0.5 text-11 leading-4 text-fg-muted hover:text-fg hover:underline">
          {folded ? "▸" : "▾"} {summary}
        </button>
      ) : null}
    </div>
  );
}

export function TimelineRail({
  entries, kinds, counts, scope, onScope, pending = false, error = null, hasMore = false, onLoadOlder, onOpen, className,
}: TimelineRailProps): React.ReactElement {
  const meta = React.useMemo(() => new Map(kinds.map((kind) => [kind.id, kind])), [kinds]);
  const kindOf = (entry: TimelineRailEntry): TimelineRailKind => meta.get(entry.kind) ?? { id: entry.kind, ...FALLBACK_KIND, label: entry.kind };
  const total = React.useMemo(() => kinds.reduce((sum, kind) => sum + (counts[kind.id] ?? 0), 0), [kinds, counts]);
  const needsMe = entries.filter((entry) => entry.needsMe).length;
  const [open, setOpen] = React.useState<ReadonlySet<string>>(new Set());
  const toggle = (id: string) => setOpen((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  // Events inside a run fold under that run's line when the run is on the page.
  const { top, children } = React.useMemo(() => {
    const runs = new Set(entries.filter((entry) => entry.kind === RUN_KIND).map((entry) => entry.id));
    const children = new Map<string, TimelineRailEntry[]>();
    const top: TimelineRailEntry[] = [];
    for (const entry of entries) {
      const parent = entry.kind !== RUN_KIND && entry.groupId && runs.has(entry.groupId) ? entry.groupId : null;
      if (parent) children.set(parent, [...(children.get(parent) ?? []), entry]); else top.push(entry);
    }
    return { top, children };
  }, [entries]);
  const visible = scope === TIMELINE_SCOPE_NEEDS_ME ? top.filter((entry) => entry.needsMe || (children.get(entry.id) ?? []).some((child) => child.needsMe)) : top;
  const groups = React.useMemo(() => visible.reduce<{ day: string; items: TimelineRailEntry[] }[]>((acc, entry) => {
    const day = dayLabel(entry.at);
    const last = acc[acc.length - 1];
    if (last && last.day === day) last.items.push(entry); else acc.push({ day, items: [entry] });
    return acc;
  }, []), [visible]);

  const chip = (id: string, label: string, count: number | null, disabled = false) => {
    const active = scope === id;
    return (
      <button key={id} type="button" aria-pressed={active} disabled={disabled} onClick={() => onScope(id)}
        className="rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
        <Chip tone={active ? "brand" : id === TIMELINE_SCOPE_NEEDS_ME ? "warning" : "muted"} variant={active ? "solid" : "soft"} className="cursor-pointer">
          {label}{count !== null ? <span className={cn("ml-1", active ? "opacity-80" : "text-fg-muted")}>{count}</span> : null}
        </Chip>
      </button>
    );
  };

  return (
    <div className={cn("flex h-full min-h-0 flex-col", className)}>
      <header className="flex flex-wrap items-center gap-1 border-b border-border-subtle px-3 py-2" role="group" aria-label="Scope">
        {chip(TIMELINE_SCOPE_ALL, "All", total)}
        {kinds.map((kind) => chip(kind.id, kind.label, counts[kind.id] ?? 0, (counts[kind.id] ?? 0) === 0))}
        {needsMe ? chip(TIMELINE_SCOPE_NEEDS_ME, "Needs you", needsMe) : null}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        {error ? <p role="alert" className="rounded-6 border border-danger-border bg-danger-bg p-2 text-12 text-danger-text">{error}</p> : null}
        {pending && entries.length === 0 ? <LoadingPanel message="Loading the timeline" density="inline" /> : null}
        {!pending && !error && visible.length === 0
          ? <EmptyState icon="activity" title="Nothing yet" description="Nothing has happened on this record in this scope." className="min-h-40 p-4" />
          : null}
        <ol>
          {groups.map((group) => (
            <li key={group.day}>
              <p className="sticky top-0 z-10 -mx-3 bg-sheet/95 px-3 py-1 text-11 font-medium uppercase tracking-wide text-fg-muted backdrop-blur">{group.day}</p>
              <ol className="relative ml-2.5 border-l border-border-subtle pb-1">
                {group.items.map((entry) => {
                  const nested = children.get(entry.id) ?? [];
                  const folded = !open.has(entry.id);
                  return (
                    <li key={`${entry.kind}:${entry.id}`} className="relative">
                      <Line entry={entry} kind={kindOf(entry)} onOpen={onOpen}
                        summary={nested.length ? summarize(nested, meta) : undefined} folded={folded}
                        onToggle={nested.length ? () => toggle(entry.id) : undefined} />
                      {nested.length && !folded ? (
                        <ol className="relative ml-4 border-l border-border-subtle">
                          {nested.map((child) => (
                            <li key={`${child.kind}:${child.id}`} className="relative">
                              <Line entry={child} kind={kindOf(child)} onOpen={onOpen} nested />
                            </li>
                          ))}
                        </ol>
                      ) : null}
                    </li>
                  );
                })}
              </ol>
            </li>
          ))}
        </ol>
        {hasMore && onLoadOlder ? (
          <div className="flex justify-center py-1">
            <Button size="sm" variant="ghost" disabled={pending} onClick={onLoadOlder}>Load older</Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
