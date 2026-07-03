import { useMemo, useState, type ReactElement, type ReactNode } from "react";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import {
  dragHasAcceptedType,
  readDndPayload,
  writeDndPayload,
  type DndPayload,
} from "../lib/dnd";
import { Tree, type TreeNode } from "../ui/tree";
import { ListEmpty } from "./resource-view-list-body";
import type { ListEmptyContent } from "./resource-view-types";

/**
 * The hierarchical View: flat `rows` carrying a self-referential `parent`
 * pointer are folded into a `Tree` (folders/files, nested records). The host
 * gives the field names; `renderRow` overrides a node's label for richer rows.
 * A peer View in a `ResourceList` — the Explorer navigator pairs it with a list.
 */
export interface TreeViewProps<
  TRow extends Record<string, unknown> = Record<string, unknown>,
> {
  rows?: readonly TRow[];
  /** Field holding the parent row's id; empty/absent means a root node. */
  parent?: keyof TRow & string;
  /** Field holding the node label. */
  label?: keyof TRow & string;
  /** Field holding a right-aligned numeric count. */
  badge?: keyof TRow & string;
  /** Field holding the stable node id. */
  rowKey?: keyof TRow & string;
  /** Field holding an icon registry name. */
  icon?: keyof TRow & string;
  selectedId?: string;
  onSelect?: (row: TRow) => void;
  renderRow?: (row: TRow) => ReactNode;
  /** Make a node draggable by returning a payload for its row, or `null`. */
  draggableRow?: (row: TRow) => DndPayload | null;
  /** Payload `type`s a node accepts on drop (drives the drag-over highlight). */
  dropAccept?: string | readonly string[];
  /** Node-level drop guard, judged on drag-over (e.g. a folder onto itself). */
  canDropOnNode?: (nodeId: string, row: TRow) => boolean;
  /** Called with the decoded payload when an accepted item drops on a node. */
  onNodeDrop?: (nodeId: string, payload: DndPayload, row: TRow) => void;
  /** Empty-state content shown when there are no nodes. */
  emptyContent?: ListEmptyContent;
  className?: string;
}

export function TreeView<TRow extends Record<string, unknown>>({
  rows = [],
  parent = "parentId" as keyof TRow & string,
  label = "name" as keyof TRow & string,
  badge,
  rowKey = "id" as keyof TRow & string,
  icon,
  selectedId,
  onSelect,
  renderRow,
  draggableRow,
  dropAccept,
  canDropOnNode,
  onNodeDrop,
  emptyContent,
  className,
}: TreeViewProps<TRow>): ReactElement {
  const t = useUiT();
  const resolvedEmptyContent = emptyContent ?? t("list.empty");
  const rowsById = useMemo(
    () => new Map(rows.map((row) => [String(row[rowKey] ?? ""), row])),
    [rows, rowKey],
  );
  const nodes = useMemo(
    () => buildTree(rows, { parent, label, badge, rowKey, icon, renderRow }),
    [rows, parent, label, badge, rowKey, icon, renderRow],
  );
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  const droppable = onNodeDrop != null;

  if (nodes.length === 0) {
    return (
      <ListEmpty className={cn("min-h-0 p-8", className)}>{resolvedEmptyContent}</ListEmpty>
    );
  }

  return (
    <Tree
      nodes={nodes}
      selectedId={selectedId}
      className={className}
      dropTargetId={dropTargetId}
      onSelect={
        onSelect
          ? (id) => {
              const row = rowsById.get(id);
              if (row) onSelect(row);
            }
          : undefined
      }
      getNodeDraggable={
        draggableRow
          ? (id) => {
              const row = rowsById.get(id);
              return row ? draggableRow(row) != null : false;
            }
          : undefined
      }
      onNodeDragStart={
        draggableRow
          ? (id, event) => {
              const row = rowsById.get(id);
              const payload = row ? draggableRow(row) : null;
              if (payload) writeDndPayload(event.dataTransfer, payload);
            }
          : undefined
      }
      canDropOnNode={
        droppable
          ? (id, event) => {
              if (!dragHasAcceptedType(event.dataTransfer, dropAccept)) {
                return false;
              }
              const row = rowsById.get(id);
              if (!row) return false;
              return canDropOnNode ? canDropOnNode(id, row) : true;
            }
          : undefined
      }
      onNodeDragOver={droppable ? (id) => setDropTargetId(id) : undefined}
      onNodeDragLeave={
        droppable
          ? (id) =>
              setDropTargetId((current) => (current === id ? null : current))
          : undefined
      }
      onNodeDrop={
        droppable
          ? (id, event) => {
              setDropTargetId(null);
              const row = rowsById.get(id);
              const payload = readDndPayload(event.dataTransfer);
              if (!row || !payload) return;
              if (canDropOnNode && !canDropOnNode(id, row)) return;
              onNodeDrop?.(id, payload, row);
            }
          : undefined
      }
    />
  );
}

function buildTree<TRow extends Record<string, unknown>>(
  rows: readonly TRow[],
  options: {
    parent: keyof TRow & string;
    label: keyof TRow & string;
    badge: (keyof TRow & string) | undefined;
    rowKey: keyof TRow & string;
    icon: (keyof TRow & string) | undefined;
    renderRow?: (row: TRow) => ReactNode;
  },
): TreeNode[] {
  const byParent = new Map<string, TRow[]>();
  for (const row of rows) {
    const parentId = String(row[options.parent] ?? "");
    const bucket = byParent.get(parentId);
    if (bucket) bucket.push(row);
    else byParent.set(parentId, [row]);
  }

  const toNode = (row: TRow): TreeNode => {
    const id = String(row[options.rowKey] ?? "");
    const count = options.badge ? Number(row[options.badge]) : undefined;
    return {
      id,
      label: options.renderRow?.(row) ?? String(row[options.label] ?? id),
      ...(options.icon && typeof row[options.icon] === "string"
        ? { icon: row[options.icon] as string }
        : {}),
      ...(count !== undefined && Number.isFinite(count) ? { count } : {}),
      children: (byParent.get(id) ?? []).map(toNode),
    };
  };

  return (byParent.get("") ?? []).map(toNode);
}
