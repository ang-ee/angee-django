import * as React from "react";
import type { DeletePreviewNode } from "@angee/refine";

import { useUiT } from "../../i18n";
import { Card } from "../../ui/card";
import { textRoleVariants } from "../../ui/text";
import { Tree, type TreeNode } from "../../ui/tree";

export interface DeletePreviewTreeProps {
  nodes: readonly DeletePreviewNode[];
}

/** The delete preview as a `Tree`: records to delete with their dependents, two levels open.
 * @kind surface
 * @owner Tree
 */
export function DeletePreviewTree({
  nodes,
}: DeletePreviewTreeProps): React.ReactElement {
  const t = useUiT();
  const treeNodes = React.useMemo(
    () => nodes.map((node, index) => toTreeNode(node, String(index), 0)),
    [nodes],
  );
  return (
    <Card className="max-h-[22rem] overflow-auto p-2 shadow-none">
      <Tree aria-label={t("deletePreview.rowsAffected")} nodes={treeNodes} />
    </Card>
  );
}

function toTreeNode(node: DeletePreviewNode, path: string, depth: number): TreeNode {
  return {
    id: `${path}:${node.objectId ?? ""}:${node.label}:${node.objectLabel}`,
    label: (
      <span className="flex min-w-0 items-baseline gap-2">
        <span className="truncate text-fg">{node.objectLabel}</span>
        {node.label ? <span className={textRoleVariants({ role: "caption", className: "shrink-0" })}>{node.label}</span> : null}
      </span>
    ),
    defaultCollapsed: depth >= 2,
    children: node.children.map((child, index) => toTreeNode(child, `${path}.${index}`, depth + 1)),
  };
}
