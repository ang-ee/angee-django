import * as React from "react";
import { ActionFormDialog, SelectionBarAction, useRouteHref } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { DuplicatesDialog } from "./DuplicatesDialog";
import { usePartiesT } from "./i18n";
import { useMergeIntoAction } from "./merge-into";

/**
 * A parties list's Merge in its selection bar, by how many records are ticked.
 * One opens "Merge into…" for it (`useMergeIntoAction`, the person form's verb):
 * the person chooses the other record and the compare page opens. Two open the
 * compare page for the pair (`parties.merge`), which owns the merge and its
 * choices. Three or more are one explicit set, the whole selection, which the
 * batch dialog (`DuplicatesDialog`) lists before anything merges; the server
 * refuses a set over its cap there, with the reason. Navigation goes through the
 * router, whose app root keeps the current `?app=`.
 */
export function MergeSelectionAction({
  ids,
  onMerged,
}: {
  ids: ReadonlySet<string>;
  onMerged?: () => void;
}): React.ReactElement {
  const t = usePartiesT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const mergeInto = useMergeIntoAction();
  const [open, setOpen] = React.useState<"pick" | "batch" | null>(null);
  const selected = [...ids];
  const [first, second] = selected;
  return (
    <>
      <SelectionBarAction surface="brand"
        onClick={() => {
          if (selected.length === 2 && first !== undefined && second !== undefined) {
            void navigate({ to: routeHref("parties.merge", { left: first, right: second }) });
          } else if (selected.length === 1) setOpen("pick");
          else if (selected.length > 2) setOpen("batch");
        }}>
        {t("merge.selection.action")}
      </SelectionBarAction>
      {open === "pick" && first !== undefined ? (
        <ActionFormDialog
          action={mergeInto}
          context={{ record: { id: first }, selectedIds: [first] }}
          open
          onOpenChange={(next) => { if (!next) setOpen(null); }}
        />
      ) : null}
      {open === "batch" ? (
        <DuplicatesDialog
          sets={[{ members: selected }]}
          onClose={(merged) => {
            setOpen(null);
            if (merged) onMerged?.();
          }}
        />
      ) : null}
    </>
  );
}

/** The selection verbs a parties list adds before the generic ones: Merge. */
export function mergeSelectionActions(ids: ReadonlySet<string>, clear?: () => void): React.ReactElement {
  return <MergeSelectionAction ids={ids} onMerged={clear} />;
}
