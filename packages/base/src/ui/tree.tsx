import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";

/**
 * Generic recursive tree primitive. Renders one flat row per node with
 * depth-driven left padding (no nested DOM branches); a leading caret
 * expands/collapses, the active row gets the brand-soft highlight. Keyboard
 * follows the WAI-ARIA tree pattern: Up/Down move between visible rows, Right
 * expands or steps in, Left collapses or steps out, Enter/Space activates.
 * Drag handlers are optional DragEvent forwarders — a consumer wires its own
 * payload codec.
 */
export interface TreeNode {
  id: string;
  label: ReactNode;
  /** Icon registry name for the row glyph. */
  icon?: string;
  /** Override colour (e.g. amber for a starred row). */
  iconColor?: string;
  /** Numeric count, right-aligned. */
  count?: number;
  children?: readonly TreeNode[];
  /** Starts collapsed when true. */
  defaultCollapsed?: boolean;
}

export interface TreeProps {
  nodes: readonly TreeNode[];
  selectedId?: string;
  onSelect?: (id: string) => void;
  getNodeDraggable?: (id: string) => boolean;
  onNodeDragStart?: (id: string, event: DragEvent<HTMLDivElement>) => void;
  onNodeDragEnd?: (id: string, event: DragEvent<HTMLDivElement>) => void;
  onNodeDragOver?: (id: string, event: DragEvent<HTMLDivElement>) => void;
  onNodeDragLeave?: (id: string, event: DragEvent<HTMLDivElement>) => void;
  canDropOnNode?: (id: string, event: DragEvent<HTMLDivElement>) => boolean;
  onNodeDrop?: (id: string, event: DragEvent<HTMLDivElement>) => void;
  dropTargetId?: string | null;
  /** Optional second cluster below the main tree (e.g. "Templates"). */
  smartLabel?: ReactNode;
  smartNodes?: readonly TreeNode[];
  className?: string;
}

/** A folder/file tree is just a `Tree`; the alias names the common use. */
export type FolderTreeProps = TreeProps;

const ROW_BASE =
  "flex items-center gap-2 h-7 px-2 rounded-md text-13 text-fg-2 cursor-pointer transition-colors hover:bg-inset outline-none focus-visible:focus-ring [&_.glyph]:size-3.5 [&_.glyph]:text-fg-subtle";
const ROW_ACTIVE =
  "bg-brand-soft text-brand-soft-text font-medium [&_.glyph]:text-brand-soft-text";
const ROW_DROP_TARGET =
  "bg-brand-soft ring-1 ring-inset ring-brand text-brand-soft-text";

interface FlatRow {
  node: TreeNode;
  depth: number;
  hasChildren: boolean;
  collapsed: boolean;
  parentId: string | null;
}

function collectInitialCollapsed(
  nodes: readonly TreeNode[],
  acc: Set<string> = new Set(),
): Set<string> {
  for (const node of nodes) {
    if (node.defaultCollapsed) acc.add(node.id);
    if (node.children) collectInitialCollapsed(node.children, acc);
  }
  return acc;
}

function flattenTree(
  nodes: readonly TreeNode[],
  collapsed: ReadonlySet<string>,
  parentId: string | null = null,
  depth = 0,
  out: FlatRow[] = [],
): FlatRow[] {
  for (const node of nodes) {
    const hasChildren = Boolean(node.children?.length);
    const isCollapsed = collapsed.has(node.id);
    out.push({ node, depth, hasChildren, collapsed: isCollapsed, parentId });
    if (hasChildren && !isCollapsed) {
      flattenTree(node.children ?? [], collapsed, node.id, depth + 1, out);
    }
  }
  return out;
}

export function Tree({
  nodes,
  selectedId,
  onSelect,
  getNodeDraggable,
  onNodeDragStart,
  onNodeDragEnd,
  onNodeDragOver,
  onNodeDragLeave,
  canDropOnNode,
  onNodeDrop,
  dropTargetId,
  smartLabel,
  smartNodes,
  className,
}: TreeProps): ReactNode {
  const [collapsed, setCollapsed] = useState<Set<string>>(() =>
    collectInitialCollapsed(nodes),
  );
  const mainFlat = useMemo(() => flattenTree(nodes, collapsed), [collapsed, nodes]);
  const smartFlat = useMemo<FlatRow[]>(
    () =>
      (smartNodes ?? []).map((node) => ({
        node,
        depth: 0,
        hasChildren: false,
        collapsed: false,
        parentId: null,
      })),
    [smartNodes],
  );
  // One flat list drives render order and keyboard nav so smart rows take part.
  const flat = useMemo(() => [...mainFlat, ...smartFlat], [mainFlat, smartFlat]);
  const flatIds = useMemo(() => flat.map((row) => row.node.id), [flat]);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const effectiveFocusedId =
    focusedId && flatIds.includes(focusedId)
      ? focusedId
      : selectedId && flatIds.includes(selectedId)
        ? selectedId
        : (flatIds[0] ?? null);
  const rowRefs = useRef(new Map<string, HTMLDivElement>());
  const pendingFocus = useRef(false);
  useEffect(() => {
    if (!pendingFocus.current || !effectiveFocusedId) return;
    pendingFocus.current = false;
    rowRefs.current.get(effectiveFocusedId)?.focus();
  }, [effectiveFocusedId]);

  const toggleCollapsed = useCallback((id: string, force?: boolean) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      const target = force ?? !next.has(id);
      if (target) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const moveFocus = useCallback((id: string) => {
    pendingFocus.current = true;
    setFocusedId(id);
  }, []);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (!effectiveFocusedId) return;
      const index = flat.findIndex((row) => row.node.id === effectiveFocusedId);
      if (index < 0) return;
      const row = flat[index];
      if (!row) return;
      switch (event.key) {
        case "ArrowDown": {
          const next = flat[index + 1];
          if (next) {
            event.preventDefault();
            moveFocus(next.node.id);
          }
          return;
        }
        case "ArrowUp": {
          const prev = flat[index - 1];
          if (prev) {
            event.preventDefault();
            moveFocus(prev.node.id);
          }
          return;
        }
        case "ArrowRight": {
          if (row.hasChildren) {
            event.preventDefault();
            if (row.collapsed) toggleCollapsed(row.node.id, false);
            else {
              const child = flat[index + 1];
              if (child && child.depth === row.depth + 1) moveFocus(child.node.id);
            }
          }
          return;
        }
        case "ArrowLeft": {
          event.preventDefault();
          if (row.hasChildren && !row.collapsed) toggleCollapsed(row.node.id, true);
          else if (row.parentId) moveFocus(row.parentId);
          return;
        }
        case "Enter":
        case " ": {
          event.preventDefault();
          onSelect?.(row.node.id);
          return;
        }
        default:
          return;
      }
    },
    [effectiveFocusedId, flat, moveFocus, onSelect, toggleCollapsed],
  );

  const renderRow = (row: FlatRow): ReactNode => (
    <TreeRow
      key={row.node.id}
      row={row}
      selectedId={selectedId}
      isFocused={row.node.id === effectiveFocusedId}
      dropTargetId={dropTargetId}
      onToggle={() => toggleCollapsed(row.node.id)}
      onSelect={onSelect}
      onFocusRow={setFocusedId}
      getNodeDraggable={getNodeDraggable}
      onNodeDragStart={onNodeDragStart}
      onNodeDragEnd={onNodeDragEnd}
      onNodeDragOver={onNodeDragOver}
      onNodeDragLeave={onNodeDragLeave}
      canDropOnNode={canDropOnNode}
      onNodeDrop={onNodeDrop}
      rowRef={(el) => {
        if (el) rowRefs.current.set(row.node.id, el);
        else rowRefs.current.delete(row.node.id);
      }}
    />
  );

  return (
    <div className={cn("min-h-0 overflow-auto text-13", className)}>
      <div role="tree" onKeyDown={handleKeyDown}>
        {mainFlat.map(renderRow)}
        {smartFlat.length ? (
          <div className="mt-4" role="group">
            {smartLabel ? (
              <span
                aria-hidden
                className="block px-2 pb-1 text-2xs font-semibold uppercase tracking-wider text-fg-subtle"
              >
                {smartLabel}
              </span>
            ) : null}
            {smartFlat.map(renderRow)}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** A folder/file tree is just a `Tree`; the alias names the common use. */
export const FolderTree = Tree;

function TreeRow({
  row,
  selectedId,
  isFocused,
  dropTargetId,
  onToggle,
  onSelect,
  onFocusRow,
  getNodeDraggable,
  onNodeDragStart,
  onNodeDragEnd,
  onNodeDragOver,
  onNodeDragLeave,
  canDropOnNode,
  onNodeDrop,
  rowRef,
}: {
  row: FlatRow;
  selectedId: string | undefined;
  isFocused: boolean;
  dropTargetId: string | null | undefined;
  onToggle: () => void;
  onSelect: ((id: string) => void) | undefined;
  onFocusRow: (id: string) => void;
  getNodeDraggable: ((id: string) => boolean) | undefined;
  onNodeDragStart: ((id: string, e: DragEvent<HTMLDivElement>) => void) | undefined;
  onNodeDragEnd: ((id: string, e: DragEvent<HTMLDivElement>) => void) | undefined;
  onNodeDragOver: ((id: string, e: DragEvent<HTMLDivElement>) => void) | undefined;
  onNodeDragLeave: ((id: string, e: DragEvent<HTMLDivElement>) => void) | undefined;
  canDropOnNode: ((id: string, e: DragEvent<HTMLDivElement>) => boolean) | undefined;
  onNodeDrop: ((id: string, e: DragEvent<HTMLDivElement>) => void) | undefined;
  rowRef: (el: HTMLDivElement | null) => void;
}): ReactNode {
  const { node, depth, hasChildren, collapsed } = row;
  // Depth-driven left padding so nested rows indent without wrapping.
  const padLeft: CSSProperties | undefined =
    depth > 0
      ? { paddingLeft: `calc(0.5rem + ${1.25 + (depth - 1) * 0.75}rem)` }
      : undefined;
  const isActive = selectedId === node.id;
  const isDraggable = getNodeDraggable?.(node.id) ?? false;
  const isDropTarget = dropTargetId === node.id;

  return (
    <div
      ref={rowRef}
      role="treeitem"
      tabIndex={isFocused ? 0 : -1}
      aria-selected={isActive}
      aria-expanded={hasChildren ? !collapsed : undefined}
      aria-level={depth + 1}
      draggable={isDraggable}
      className={cn(ROW_BASE, isActive && ROW_ACTIVE, isDropTarget && ROW_DROP_TARGET)}
      style={padLeft}
      onClick={() => {
        onFocusRow(node.id);
        onSelect?.(node.id);
      }}
      onFocus={() => onFocusRow(node.id)}
      onDragStart={(event) => isDraggable && onNodeDragStart?.(node.id, event)}
      onDragEnd={(event) => isDraggable && onNodeDragEnd?.(node.id, event)}
      onDragOver={(event) => {
        if (!onNodeDrop) return;
        if (canDropOnNode && !canDropOnNode(node.id, event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        onNodeDragOver?.(node.id, event);
      }}
      onDragLeave={(event) => onNodeDragLeave?.(node.id, event)}
      onDrop={(event) => {
        if (!onNodeDrop) return;
        if (canDropOnNode && !canDropOnNode(node.id, event)) return;
        event.preventDefault();
        onNodeDrop(node.id, event);
      }}
    >
      <button
        type="button"
        aria-label={hasChildren ? (collapsed ? "Expand" : "Collapse") : undefined}
        className={cn("grid place-content-center", !hasChildren && "invisible")}
        disabled={!hasChildren}
        tabIndex={-1}
        onClick={(event) => {
          event.stopPropagation();
          onToggle();
        }}
      >
        <Glyph
          name="chevron-right"
          className={cn("transition-transform", hasChildren && !collapsed && "rotate-90")}
        />
      </button>
      {node.icon ? (
        <span style={node.iconColor ? { color: node.iconColor } : undefined}>
          <Glyph name={node.icon} />
        </span>
      ) : null}
      <span className="flex-1 truncate">{node.label}</span>
      {typeof node.count === "number" ? (
        <span className="text-2xs tabular-nums text-fg-subtle">
          {node.count.toLocaleString()}
        </span>
      ) : null}
    </div>
  );
}
