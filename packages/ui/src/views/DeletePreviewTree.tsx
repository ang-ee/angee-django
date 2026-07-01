import * as React from "react";
import type { DeletePreviewNode } from "@angee/refine";

import { Glyph } from "../chrome/Glyph";

export interface DeletePreviewTreeProps {
  nodes: readonly DeletePreviewNode[];
}

export function DeletePreviewTree({
  nodes,
}: DeletePreviewTreeProps): React.ReactElement {
  return (
    <div className="max-h-[22rem] overflow-auto rounded-6 border border-border-subtle bg-sheet-2 p-2">
      <ul className="space-y-1">
        {nodes.map((node, index) => (
          <DeletePreviewTreeItem
            key={treeNodeKey(node, index)}
            node={node}
            depth={0}
          />
        ))}
      </ul>
    </div>
  );
}

function DeletePreviewTreeItem({
  node,
  depth,
}: {
  node: DeletePreviewNode;
  depth: number;
}): React.ReactElement {
  const hasChildren = node.children.length > 0;
  const [expanded, setExpanded] = React.useState(() => depth < 2);
  const labelId = React.useId();
  const content = (
    <span className="flex min-w-0 items-baseline gap-2">
      <span id={labelId} className="truncate text-fg">
        {node.objectLabel}
      </span>
      {node.label ? (
        <span className="shrink-0 text-12 text-fg-muted">{node.label}</span>
      ) : null}
    </span>
  );

  return (
    <li>
      <div
        className="flex min-w-0 items-center gap-1 rounded-6 px-1 py-0.5 text-13"
        style={{ paddingLeft: `${depth * 1.125 + 0.25}rem` }}
      >
        {hasChildren ? (
          <button
            type="button"
            className="grid size-5 shrink-0 place-content-center rounded-6 text-fg-muted outline-none hover:bg-inset hover:text-fg focus-visible:focus-ring"
            aria-expanded={expanded}
            aria-labelledby={labelId}
            onClick={() => setExpanded((current) => !current)}
          >
            <Glyph name={expanded ? "chevron-down" : "chevron-right"} />
          </button>
        ) : (
          <span className="size-5 shrink-0" aria-hidden />
        )}
        {content}
      </div>
      {hasChildren && expanded ? (
        <ul className="space-y-1">
          {node.children.map((child, index) => (
            <DeletePreviewTreeItem
              key={treeNodeKey(child, index)}
              node={child}
              depth={depth + 1}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function treeNodeKey(node: DeletePreviewNode, index: number): string {
  return `${node.objectId ?? ""}:${node.label}:${node.objectLabel}:${index}`;
}
