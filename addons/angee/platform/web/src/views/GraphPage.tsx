import { type ReactElement } from "react";
import { useNavigate } from "@tanstack/react-router";
import { parseAsString, useQueryState } from "nuqs";

import {
  GraphView,
  type GraphViewNode,
  type GraphViewNodeStyle,
} from "@angee/ui";

import { usePlatformModelGraph } from "../lib/explorer";
import { modelDetailPath } from "../lib/paths";

const NODE_STYLES: Record<"model", GraphViewNodeStyle> = {
  model: {
    width: 208,
    height: 64,
    borderColor: "var(--border-strong)",
    badgeTone: "neutral",
  },
};

export function GraphPage(): ReactElement {
  const navigate = useNavigate();
  const [modelScope] = useQueryState("model", parseAsString);
  const { nodes, edges, error } = usePlatformModelGraph({ model: modelScope });

  if (error) {
    return (
      <div className="px-3 py-6 text-13 text-danger-text">
        {error.message}
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
        onNodeClick={(node: GraphViewNode<"model">) =>
          navigate({ to: modelDetailPath(node.id) })
        }
      />
    </div>
  );
}
