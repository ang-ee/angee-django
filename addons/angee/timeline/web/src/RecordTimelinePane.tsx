import { useAuthoredQuery } from "@angee/refine";
import { TIMELINE_SCOPE_ALL, TimelineRail, useChatter, type TimelineRailEntry, type TimelineRailKind } from "@angee/ui";
import { useResourceRecordHrefLookup, type ChatterViewContext } from "@angee/ui/runtime";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { TimelineDocument } from "./documents.console";

export const TIMELINE_TAB_ID = "timeline.rail";

const PAGE_SIZE = 25;

/**
 * Each kind's one verb and where it leads. A tab id selects the kind's own
 * `record#aside` drill-in; without one the verb opens the target's page.
 * Spike note: the kind catalogue is local to the timeline addon; the refactor
 * lets each source declare its label, glyph, verb and tab.
 */
const KINDS: Record<string, TimelineRailKind & { tab?: string }> = {
  // Icon names are registered by their owners: `workflow` by workflows, `pencil`, `check`, `comments` by the framework, `channel` by messaging.
  run: { id: "run", label: "Runs", icon: "workflow", tone: "info", verb: "Open run", tab: "workflows.run" },
  decision: { id: "decision", label: "Decisions", icon: "check", tone: "success", verb: "Open decision" },
  change: { id: "change", label: "Changes", icon: "pencil", tone: "muted", verb: "History", tab: "provenance.history" },
  comment: { id: "comment", label: "Comments", icon: "comments", tone: "info", verb: "Discussion", tab: "messaging.comments" },
  conversation: { id: "conversation", label: "Conversations", icon: "channel", tone: "muted", verb: "Open conversation" },
};

/** Live changes to any of these refetch the rail; the union read spans them. */
const INVALIDATE_MODELS = [
  "workflows.WorkflowRun", "workflows.StepRun", "decisions.Decision", "decisions.DecisionRecord",
  "provenance.Changeset", "messaging.Message", "messaging.ThreadAttachment",
];

export function RecordTimelinePane({ context }: { context: ChatterViewContext }): React.ReactElement {
  const model = context.route?.canonicalLabel ?? context.route?.modelLabel ?? "";
  const id = context.view.kind === "record" ? context.view.sqid ?? "" : "";
  const [scope, setScope] = React.useState<string>(TIMELINE_SCOPE_ALL);
  const [limit, setLimit] = React.useState(PAGE_SIZE);
  const { setActiveTab } = useChatter();
  const recordHref = useResourceRecordHrefLookup();
  const navigate = useNavigate();
  const variables = React.useMemo(
    () => ({ model, id, kinds: scope === TIMELINE_SCOPE_ALL ? null : [scope], limit }),
    [model, id, scope, limit],
  );
  const query = useAuthoredQuery(TimelineDocument, variables, {
    enabled: Boolean(model && id),
    keepPreviousData: true,
    models: INVALIDATE_MODELS,
    records: model && id ? [{ model, id }] : undefined,
  });
  const page = query.data?.timeline;
  const entries = React.useMemo<readonly TimelineRailEntry[]>(() => (page?.entries ?? []).map((entry) => ({
    id: entry.id, at: entry.at, kind: entry.kind, title: entry.title, actor: entry.actor, detail: entry.detail, needsMe: entry.needs_me,
    groupId: entry.group_id,
  })), [page?.entries]);
  const kinds = React.useMemo<readonly TimelineRailKind[]>(
    () => (page?.kinds ?? []).map((kind) => KINDS[kind] ?? { id: kind, label: kind, icon: "activity", tone: "muted", verb: "Open" }),
    [page?.kinds],
  );
  const counts = React.useMemo(() => Object.fromEntries((page?.counts ?? []).map((row) => [row.kind, row.count])), [page?.counts]);
  const targets = React.useMemo(
    () => new Map((page?.entries ?? []).map((entry) => [`${entry.kind}:${entry.id}`, entry])),
    [page?.entries],
  );
  const open = React.useCallback((entry: TimelineRailEntry) => {
    const tab = KINDS[entry.kind]?.tab;
    if (tab) {
      setActiveTab(tab, { from: TIMELINE_TAB_ID });
      return;
    }
    const target = targets.get(`${entry.kind}:${entry.id}`);
    const href = target?.target_model && target.target_id ? recordHref(target.target_model, target.target_id) : undefined;
    if (href) void navigate({ to: href });
  }, [navigate, recordHref, setActiveTab, targets]);

  return (
    <TimelineRail
      entries={entries}
      kinds={kinds}
      counts={counts}
      scope={scope}
      onScope={(next) => { setScope(next); setLimit(PAGE_SIZE); }}
      pending={query.isLoading || query.isFetching}
      error={query.error ? query.error.message : null}
      hasMore={Boolean(page?.has_more)}
      onLoadOlder={() => setLimit((current) => current + PAGE_SIZE)}
      onOpen={open}
      className="h-full"
    />
  );
}
