import { useMemo, type ReactElement } from "react";

import { GraphView, type GraphViewNodeStyle } from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import { PLATFORM_EXPLORER_QUERY, type PlatformExplorerResult } from "../documents";
import { modelGraphEdges, modelGraphNodes } from "../lib/rows";

const NODE_STYLES: Record<"model", GraphViewNodeStyle> = {
  model: {
    width: 208,
    height: 64,
    borderColor: "var(--border-strong)",
    badgeTone: "neutral",
  },
};

export function GraphPage(): ReactElement {
  const query = useAuthoredQuery<PlatformExplorerResult>(PLATFORM_EXPLORER_QUERY);
  const explorer = query.data?.platformExplorer;
  const nodes = useMemo(
    () => modelGraphNodes(explorer?.models ?? []),
    [explorer],
  );
  const edges = useMemo(
    () => modelGraphEdges(explorer?.edges ?? []),
    [explorer],
  );

  if (query.error) {
    return (
      <div className="px-3 py-6 text-13 text-danger-text">
        {query.error.message}
      </div>
    );
  }

  return (
    <div className="h-full min-h-0 bg-sheet">
      <GraphView
        nodes={nodes}
        edges={edges}
        nodeStyles={NODE_STYLES}
        layout={{ rankdir: "LR" }}
        className="h-full"
      />
    </div>
  );
}
