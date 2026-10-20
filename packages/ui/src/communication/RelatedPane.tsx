import * as React from "react";

import { EmptyState } from "../fragments/EmptyState";
import { useUiT } from "../i18n";
import { modelChain, useContainer, type ChatterViewContext, type ComposedContainerChild, type RelatedGroupContent } from "../runtime";
import { PaneLabel, PaneRows } from "./pane-list";
import { ChatterCountProbe, useChatterCounts, type CountProbeProps } from "./count-probe";

/** The groups eligible on this view, in composed order. */
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

/** Sum independently mounted group readers; route and permission changes can
 * change the eligible groups without changing any reader's hook order. */
export function RelatedCountProbe({ id, context, onCount }: CountProbeProps): React.ReactElement {
  const groups = useRelatedGroups(context);
  const [counts, publishCount] = useChatterCounts();
  const total = groups.reduce<number | undefined>((sum, group) => {
    const count = counts[group.id];
    return count === undefined ? sum : (sum ?? 0) + count;
  }, undefined);
  React.useEffect(() => {
    onCount(id, total);
    return () => onCount(id, undefined);
  }, [id, total, onCount]);
  return <>{groups.map((group) => group.content.useCount ? (
    <ChatterCountProbe key={group.id} id={group.id} useCount={group.content.useCount}
      context={context} onCount={publishCount} />
  ) : null)}</>;
}

/** The Related tab: every eligible group, its label with count and verb, then its rows. */
export function RelatedPane({ context }: { context: ChatterViewContext }): React.ReactElement {
  const t = useUiT();
  const groups = useRelatedGroups(context);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
        {groups.length === 0 ? (
          <EmptyState icon="link" title={t("related.empty.title")} description={t("related.empty.description")} className="min-h-40 p-4" />
        ) : (
          <PaneRows>
            {groups.map((group) => <RelatedGroup key={group.id} group={group} context={context} />)}
          </PaneRows>
        )}
      </div>
    </div>
  );
}

function RelatedGroup({ group, context }: { group: ComposedContainerChild<RelatedGroupContent>; context: ChatterViewContext }): React.ReactElement | null {
  const count = group.content.useCount?.(context);
  const verb = group.content.useVerb?.(context) ?? false;
  // A group with neither rows nor a verb is absent, not empty.
  if (count === 0 && !verb) return null;
  return <>{group.content.render(context, (action) =>
    <PaneLabel count={count} action={verb ? action : null}>{group.content.label}</PaneLabel>)}</>;
}
