import * as React from "react";
import type { Row } from "@angee/metadata";
import { PaneLabel, PaneRows } from "../../../communication/pane-list";
import { ListStatusMessage } from "../../../fragments/ListStatusMessage";
import { LoadingPanel } from "../../../fragments/LoadingPanel";
import { useUiT } from "../../../i18n";
import { ResourcePaneFooter, type ResourceToolbarProps } from "../../../toolbars/ResourceToolbar";
import { GroupedScopePager } from "../GroupedScopePager";
import { groupedRowLabel, ListEmpty } from "../resource-view-list-body";
import type { ResourceViewGroup } from "../resource-view-model";
import type { GroupedResourceViewSurface, ResourceViewSurface } from "../resource-view-surface";
import type { ListEmptyContent, ListViewProps } from "../resource-view-types";

/** Pane anatomy over the collection owner's row stream; no query or expansion state. */
export function PaneListBody<TRow extends Row>({ surface, renderRow, renderGroupLabel, groupStack, emptyContent, toolbar }: {
  surface: (Pick<ResourceViewSurface<TRow>, "kind" | "rowModels"> & { list: { fetching: boolean } }) | GroupedResourceViewSurface<TRow>;
  renderRow: (row: TRow) => React.ReactNode;
  renderGroupLabel?: ListViewProps<TRow>["renderGroupLabel"];
  groupStack: readonly ResourceViewGroup[];
  emptyContent: ListEmptyContent;
  toolbar?: ResourceToolbarProps;
}): React.ReactElement {
  const t = useUiT();
  const empty = surface.kind === "grouped" ? surface.groupedItems.length === 0 : surface.rowModels.length === 0;
  return (
    <>
    <div className="min-h-0 flex-1 overflow-auto px-2 py-1">
      {empty ? surface.list.fetching ? <LoadingPanel /> : <ListEmpty className="p-4">{emptyContent}</ListEmpty> : (
      <PaneRows>
        {surface.kind === "flat" ? surface.rowModels.map((row) => {
          if (!row.getIsGrouped()) return <li key={row.id}>{renderRow(row.original)}</li>;
          const label = groupedRowLabel(row, groupStack, t);
          const count = row.getLeafRows().length;
          const key = Object.fromEntries([...row.getParentRows(), row].map((group) => [
            group.groupingColumnId!, group.getGroupingValue(group.groupingColumnId!),
          ]));
          return <PaneLabel key={row.id} count={count} folded={!row.getIsExpanded()}
            action={renderGroupLabel?.({ label, depth: row.depth, bucket: { key, count } })}
            onToggle={() => row.toggleExpanded()}>{label}</PaneLabel>;
        })
          : surface.groupedItems.map((item) => {
            switch (item.kind) {
              case "record": return <li key={item.itemKey}>{renderRow(item.row.original)}</li>;
              case "groupHeader": return <React.Fragment key={item.bucketKey}>
                <PaneLabel count={item.count} folded={!item.expanded}
                  onToggle={item.expandable ? () => surface.toggleGroup(item.bucketKey) : undefined}
                  action={renderGroupLabel?.(item)}>{item.label}</PaneLabel>
                {item.expanded && item.pager ? <li><GroupedScopePager pager={item.pager} label={item.label}
                  onPageChange={surface.setScopePage} onPageSizeChange={surface.setScopePageSize} t={t} /></li> : null}
                {!item.expandable ? <li className="px-1 text-12 text-fg-muted">{item.unavailableMessage}</li> : null}
              </React.Fragment>;
              case "skeleton": return <li key={item.itemKey}><LoadingPanel density="inline" /></li>;
              case "status": return <li key={item.itemKey}><ListStatusMessage tone={item.tone}>{item.message}</ListStatusMessage></li>;
              default: return assertNever(item);
            }
          })}
      </PaneRows>
      )}
    </div>
    {toolbar ? <ResourcePaneFooter {...toolbar} /> : null}
    </>
  );
}

function assertNever(item: never): never {
  throw new Error(`Unknown pane list item: ${JSON.stringify(item)}`);
}
