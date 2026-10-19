import * as React from "react";

import { EmptyState } from "../fragments/EmptyState";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { useUiT } from "../i18n";
import { PaneToolbar } from "../layouts/PaneToolbar";
import { cn } from "../lib/cn";
import type { TimelineKindContent } from "../runtime";
import { Button } from "../ui/button";
import { PaneLabel, PaneRow, PaneRows, ScopeChip } from "./pane-list";
import { formatDate, formatDateTime } from "../widgets/date-format";

/**
 * The record timeline rail: the index of what happened, newest first, one line per
 * event. A line is a pointer: glyph, `title · detail`, time, actor and a folded
 * summary, one verb; never a body, never an action. The owning addon feeds it
 * entries and kind presentations; the rail owns the visual part once: day labels,
 * scope chips in the pane toolbar, keyset paging, loading, empty, partial-failure
 * and error states. Schema-independent.
 */

export interface TimelineRailEntry {
  /** The event's stable key, unique across kinds. */
  key: string;
  /** ISO datetime. */
  at: string;
  kind: string;
  title: string;
  actor: string;
  detail?: string;
  needsMe?: boolean;
  /** The folded summary of what happened inside (a run's "3 changes · 5 decisions"). */
  folded?: string;
  /** Where the verb leads, as an in-app href so copy and open-in-new-tab work. */
  href?: string;
}

export interface TimelineRailKind extends TimelineKindContent {
  id: string;
  count: number;
}

export const TIMELINE_SCOPE_ALL = "all";
export const TIMELINE_SCOPE_NEEDS_ME = "needs-me";

export interface TimelineRailProps {
  entries: readonly TimelineRailEntry[];
  /** The kinds this record can carry, in their declared order, with counts. */
  kinds: readonly TimelineRailKind[];
  needsMe: number;
  scope: string;
  onScope: (scope: string) => void;
  pending?: boolean;
  error?: string | null;
  onRetry?: () => void;
  hasMore?: boolean;
  onLoadOlder?: () => void;
  onOpen: (entry: TimelineRailEntry) => void;
  className?: string;
}

const MAX_SCOPE_CHIPS = 2;

export function TimelineRail({
  entries, kinds, needsMe, scope, onScope, pending = false, error = null, onRetry, hasMore = false, onLoadOlder, onOpen, className,
}: TimelineRailProps): React.ReactElement {
  const t = useUiT();
  const presentation = React.useMemo(() => new Map(kinds.map((kind) => [kind.id, kind])), [kinds]);
  const total = kinds.reduce((sum, kind) => sum + kind.count, 0);
  const sorted = React.useMemo(() => [...entries].sort((a, b) => Date.parse(b.at) - Date.parse(a.at)), [entries]);
  const chips = [
    { id: TIMELINE_SCOPE_ALL, label: t("timeline.scope.all"), count: total },
    ...(needsMe ? [{ id: TIMELINE_SCOPE_NEEDS_ME, label: t("timeline.scope.needsMe"), count: needsMe, tone: "warning" as const }] : []),
    ...kinds.map((kind) => ({ id: kind.id, label: kind.label, count: kind.count })),
  ];
  const shown = chips.slice(0, MAX_SCOPE_CHIPS + 1);
  const rest = chips.slice(shown.length);

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col", className)}>
      <PaneToolbar
        scope={<>
          {shown.map((chip) => <ScopeChip key={chip.id} label={chip.label} count={chip.count} active={scope === chip.id} tone={"tone" in chip ? chip.tone : undefined} onClick={() => onScope(chip.id)} />)}
          {rest.length ? (
            <select aria-label={t("timeline.scope.more")} value={rest.some((chip) => chip.id === scope) ? scope : ""} onChange={(event) => onScope(event.target.value || TIMELINE_SCOPE_ALL)}
              className="h-5 rounded-full bg-transparent px-1 text-12 text-fg-muted hover:bg-canvas">
              <option value="">{t("timeline.scope.moreCount", { count: rest.length })}</option>
              {rest.map((chip) => <option key={chip.id} value={chip.id}>{typeof chip.label === "string" ? `${chip.label} · ${chip.count}` : chip.id}</option>)}
            </select>
          ) : null}
        </>}
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
        {error ? (
          <div role="alert" className="m-1 rounded-8 border border-danger-border bg-danger-bg p-3 text-13 text-danger-text">
            <p>{error}</p>
            {onRetry ? <Button size="sm" variant="secondary" className="mt-2" onClick={onRetry}>{t("timeline.retry")}</Button> : null}
          </div>
        ) : null}
        {pending && sorted.length === 0 && !error ? <LoadingPanel message={t("timeline.loading")} density="inline" /> : null}
        {!pending && !error && sorted.length === 0 ? (
          <EmptyState icon="activity" title={t("timeline.empty.title")} description={t("timeline.empty.description")} className="min-h-40 p-4" />
        ) : null}
        <PaneRows>
          {sorted.map((entry, index) => {
            const day = formatDate(entry.at);
            const previous = index ? formatDate(sorted[index - 1]!.at) : undefined;
            const kind = presentation.get(entry.kind);
            return (
              <React.Fragment key={entry.key}>
                {day !== previous ? <PaneLabel>{day}</PaneLabel> : null}
                <li>
                  <PaneRow
                    icon={kind?.icon ?? "activity"}
                    tone={kind?.tone ?? "muted"}
                    title={entry.title}
                    detail={entry.detail}
                    meta={<time dateTime={entry.at} title={formatDateTime(entry.at)}>{new Date(entry.at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</time>}
                    sub={`${entry.actor}${entry.folded ? ` · ${entry.folded}` : ""}`}
                    verb={kind?.verb}
                    needsMe={entry.needsMe}
                    href={entry.href}
                    onOpen={() => onOpen(entry)}
                    label={typeof kind?.verb === "string" ? kind.verb : undefined}
                  />
                </li>
              </React.Fragment>
            );
          })}
          {hasMore && onLoadOlder ? (
            <li className="py-2 text-center"><Button size="sm" variant="ghost" disabled={pending} onClick={onLoadOlder}>{t("timeline.older")}</Button></li>
          ) : null}
        </PaneRows>
      </div>
    </div>
  );
}
