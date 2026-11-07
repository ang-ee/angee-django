import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";

import { EmptyState } from "../fragments/EmptyState";
import { useUiT } from "../i18n";
import { modelChain, useContainer, type ChatterViewContext, type ComposedContainerChild, type RelatedGroupContent } from "../runtime";
import { PaneLabel, PaneRows } from "./pane-list";
import { ChatterCountProbe, PublishedCount, useChatterCounts, type CountProbeProps } from "./count-probe";
import { RelatedCounts } from "./documents";

/** The groups eligible on this view, in composed order.
 * @owner RelatedPane
 */
export function useRelatedGroups<T extends RelatedGroupContent = RelatedGroupContent>(context: ChatterViewContext): readonly ComposedContainerChild<T>[] {
  const models = React.useMemo(
    () => modelChain(context.route?.canonicalLabel, context.route?.modelLabel),
    [context.route?.canonicalLabel, context.route?.modelLabel],
  );
  const children = useContainer<T>("related#groups", { models });
  return React.useMemo(
    () => children.filter(({ content }) => context.view.kind === "record"
      && (!content.edge || context.route?.recordEdges?.includes(content.edge.model))
      && (content.when?.(context) ?? true)),
    [children, context],
  );
}

/** Sum each eligible group's count: one shared read counts every declared related count,
 * and while Links renders, row-derived pane counts mount as independent keyed
 * readers, so eligibility changes never change a reader's hook order. A badge
 * waits, and requests nothing, until every group can count.
 * @kind surface
 */
export function RelatedCountProbe({ id, context, onCount, includePaneCounts = false }: CountProbeProps & { includePaneCounts?: boolean }): React.ReactElement {
  const groups = useRelatedGroups(context);
  const [counts, publishCount] = useChatterCounts();
  const paneGroups = React.useMemo(
    () => includePaneCounts ? groups.filter((group) => group.content.usePaneCount) : [],
    [groups, includePaneCounts],
  );
  const countedGroups = React.useMemo(
    () => groups.filter((group) => group.content.relatedCount && !paneGroups.includes(group)),
    [groups, paneGroups],
  );
  const canCount = paneGroups.length + countedGroups.length === groups.length;
  const total = canCount && groups.every((group) => counts[group.id] !== undefined)
    ? groups.reduce((sum, group) => sum + counts[group.id]!, 0)
    : undefined;
  React.useEffect(() => {
    onCount(id, total);
    return () => onCount(id, undefined);
  }, [id, total, onCount]);
  if (!canCount) return <></>;
  return <>
    {countedGroups.length > 0 ? <GroupCountsProbe context={context} groups={countedGroups} onCount={publishCount} /> : null}
    {paneGroups.map((group) => (
      <ChatterCountProbe key={group.id} id={group.id} useCount={group.content.usePaneCount!}
        context={context} onCount={publishCount} />
    ))}
  </>;
}

/** One `related_counts` read for every given group's related count, named and published by group id. */
function GroupCountsProbe({ context, groups, onCount }: {
  context: ChatterViewContext;
  groups: readonly ComposedContainerChild<RelatedGroupContent>[];
  onCount: CountProbeProps["onCount"];
}): React.ReactElement {
  const model = context.route?.canonicalLabel ?? context.route?.modelLabel ?? "";
  const record = context.view.kind === "record" ? context.view.sqid ?? "" : "";
  const ids = groups.map((group) => group.id).sort();
  const models = [...new Set(groups.flatMap((group) => group.content.relatedCount!.models))].sort();
  const { data } = useAuthoredQuery(RelatedCounts, { model, id: record, groups: ids }, {
    enabled: Boolean(model && record), models,
  });
  const counts = new Map(data?.related_counts.map(({ group, count }) => [group, count] as const));
  return <>{groups.map((group) => (
    <PublishedCount key={group.id} id={group.id} count={counts.get(group.id)} onCount={onCount} />
  ))}</>;
}

/** The Links tab: every eligible group, its label with count and verb, then its rows.
 * @kind surface
 */
export function RelatedPane({ context }: { context: ChatterViewContext }): React.ReactElement {
  const t = useUiT();
  const groups = useRelatedGroups(context);
  const [counts, publishCount] = useChatterCounts();
  const countedGroups = React.useMemo(
    () => groups.filter((group) => group.content.relatedCount && !group.content.usePaneCount),
    [groups],
  );
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {countedGroups.length > 0 ? <GroupCountsProbe context={context} groups={countedGroups} onCount={publishCount} /> : null}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
        {groups.length === 0 ? (
          <EmptyState icon="link" title={t("related.empty.title")} description={t("related.empty.description")} className="min-h-40 p-4" />
        ) : (
          <PaneRows>
            {groups.map((group) => <RelatedGroup key={group.id} group={group} context={context} groupCount={counts[group.id]} />)}
          </PaneRows>
        )}
      </div>
    </div>
  );
}

function RelatedGroup({ group, context, groupCount }: {
  group: ComposedContainerChild<RelatedGroupContent>;
  context: ChatterViewContext;
  groupCount: number | undefined;
}): React.ReactElement | null {
  const count = group.content.usePaneCount ? group.content.usePaneCount(context) : groupCount;
  const verb = group.content.useVerb?.(context) ?? false;
  // A group with neither rows nor a verb is absent, not empty.
  if (count === 0 && !verb) return null;
  return <>{group.content.render(context, (action) =>
    <PaneLabel count={count} action={verb ? action : null}>{group.content.label}</PaneLabel>)}</>;
}
