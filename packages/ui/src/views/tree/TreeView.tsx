import { useMemo, useState, type ReactElement, type ReactNode } from "react";

import { useUiT } from "../../i18n";
import { cn } from "../../lib/cn";
import {
  dragHasAcceptedType,
  readDndPayload,
  writeDndPayload,
  type DndPayload,
} from "../../lib/dnd";
import { Tree, type TreeNode } from "../../ui/tree";
import { ListEmpty } from "../resource/resource-view-list-body";
import type { ListEmptyContent } from "../resource/resource-view-types";

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
  /**
   * Field holding a boolean that declares the row is expandable even when its
   * children are not loaded yet (lazy trees). Opt-in: when omitted, a node's
   * caret follows the loaded children exactly as before. Pair it with
   * {@link TreeViewProps.onExpand} to fetch children on demand.
   */
  hasChildren?: keyof TRow & string;
  /** Field holding a boolean that shows a spinner while the row's children load. */
  loading?: keyof TRow & string;
  selectedId?: string;
  onSelect?: (row: TRow) => void;
  /** Fired with a node id when it expands and its children are not loaded yet. */
  onExpand?: (nodeId: string) => void;
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
  /**
   * An editable row shown in the tree where a new node will go: first under
   * `parentId`, or first at the root when `parentId` is null. It is not a row of
   * the data and cannot be selected; the caller renders the field.
   */
  insertion?: { parentId: string | null; node: ReactNode } | null;
  className?: string;
}

/** The synthetic id of the insertion row; never a data row's id.
 * @owner TreeView
 */
export const TREE_INSERTION_ID = "__tree-insertion__";

/**
 * @kind surface
 */
export function TreeView<TRow extends Record<string, unknown>>({
  rows = [],
  parent = "parentId" as keyof TRow & string,
  label = "name" as keyof TRow & string,
  badge,
  rowKey = "id" as keyof TRow & string,
  icon,
  hasChildren,
  loading,
  selectedId,
  onSelect,
  onExpand,
  renderRow,
  draggableRow,
  dropAccept,
  canDropOnNode,
  onNodeDrop,
  emptyContent,
  insertion = null,
  className,
}: TreeViewProps<TRow>): ReactElement {
  const t = useUiT();
  const resolvedEmptyContent = emptyContent ?? t("list.empty");
  const rowsById = useMemo(
    () => new Map(rows.map((row) => [String(row[rowKey] ?? ""), row])),
    [rows, rowKey],
  );
  const nodes = useMemo(
    () =>
      buildTree(rows, {
        parent,
        label,
        badge,
        rowKey,
        icon,
        hasChildren,
        loading,
        renderRow,
      }),
    [rows, parent, label, badge, rowKey, icon, hasChildren, loading, renderRow],
  );
  const nodesWithInsertion = useMemo(
    () => (insertion ? withInsertion(nodes, insertion.parentId, insertion.node) : nodes),
    [nodes, insertion],
  );
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  const droppable = onNodeDrop != null;

  if (nodesWithInsertion.length === 0) {
    return (
      <ListEmpty className={cn("min-h-0 p-8", className)}>{resolvedEmptyContent}</ListEmpty>
    );
  }

  return (
    <Tree
      nodes={nodesWithInsertion}
      selectedId={selectedId}
      onExpand={onExpand}
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

/** Place the insertion row first under its parent (or first at the root), expanding the parent. */
function withInsertion(nodes: TreeNode[], parentId: string | null, node: ReactNode): TreeNode[] {
  // The row is a form, not a node: its keys and clicks stay with the field, so Enter
  // submits the title instead of selecting the row and arrows edit instead of moving.
  const label = (
    <div className="contents" onKeyDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
      {node}
    </div>
  );
  const row: TreeNode = { id: TREE_INSERTION_ID, label, children: [] };
  if (parentId === null) return [row, ...nodes];
  let placed = false;
  const visit = (list: TreeNode[]): TreeNode[] =>
    list.map((item) => {
      const children = item.children ?? [];
      if (item.id === parentId) {
        placed = true;
        return { ...item, hasChildren: true, children: [row, ...children] };
      }
      return children.length ? { ...item, children: visit([...children]) } : item;
    });
  const result = visit(nodes);
  return placed ? result : [row, ...nodes];
}

function buildTree<TRow extends Record<string, unknown>>(
  rows: readonly TRow[],
  options: {
    parent: keyof TRow & string;
    label: keyof TRow & string;
    badge: (keyof TRow & string) | undefined;
    rowKey: keyof TRow & string;
    icon: (keyof TRow & string) | undefined;
    hasChildren: (keyof TRow & string) | undefined;
    loading: (keyof TRow & string) | undefined;
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
      // Only carry the caret/spinner facts a row explicitly declares: a row that
      // leaves the field unset (e.g. a synthetic scope) keeps the default,
      // loaded-children caret behavior.
      ...(options.hasChildren && row[options.hasChildren] !== undefined
        ? { hasChildren: Boolean(row[options.hasChildren]) }
        : {}),
      ...(options.loading && row[options.loading] !== undefined
        ? { loading: Boolean(row[options.loading]) }
        : {}),
      children: (byParent.get(id) ?? []).map(toNode),
    };
  };

  return (byParent.get("") ?? []).map(toNode);
}
