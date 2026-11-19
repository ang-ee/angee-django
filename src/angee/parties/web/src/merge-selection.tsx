import * as React from "react";
import { SelectionBarAction, Tooltip, useRouteHref } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { usePartiesT } from "./i18n";

/**
 * A parties list's Merge in its selection bar. Exactly two selected records open
 * the compare-and-merge page for that pair (`parties.merge`), which owns the
 * merge, its confirmation and its refusals; the list only chooses the pair. Any
 * other count keeps the verb in the bar, disabled, with its reason as the
 * tooltip. The comparison is the parties route wherever the list is mounted, and
 * its Back returns to this list (`MergePage`).
 */
export function MergeSelectionAction({ ids }: { ids: ReadonlySet<string> }): React.ReactElement {
  const t = usePartiesT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const [left, right, ...rest] = ids;
  const pair = left !== undefined && right !== undefined && rest.length === 0 ? { left, right } : null;
  const reason = pair ? undefined : t(ids.size < 2 ? "merge.selection.tooFew" : "merge.selection.tooMany");
  return (
    // A disabled reason is the verb's tooltip; the verb stays focusable so keyboard users reach it.
    <Tooltip label={reason} side="bottom">
      <SelectionBarAction surface="brand" disabled={!pair} focusableWhenDisabled={Boolean(reason)}
        onClick={() => {
          if (pair) void navigate({ to: routeHref("parties.merge", pair) });
        }}>
        {t("merge.selection.action")}
      </SelectionBarAction>
    </Tooltip>
  );
}

/** The selection verbs a parties list adds before the generic ones: Merge. */
export function mergeSelectionActions(ids: ReadonlySet<string>): React.ReactElement {
  return <MergeSelectionAction ids={ids} />;
}
