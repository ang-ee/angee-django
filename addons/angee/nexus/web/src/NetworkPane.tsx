import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { GraphView, InlineEmpty, LoadingPanel } from "@angee/ui";

import { NexusNetworkPane } from "./documents";
import { edgeStyles, graphEdges, graphNodes, nodeStyles } from "./graph-data";
import { useNexusT } from "./i18n";

/** One-hop Party network rendered through the record chatter contribution. */
export function NetworkPane({ partyId }: { partyId: string }): React.ReactElement {
  const t = useNexusT();
  const query = useAuthoredQuery(
    NexusNetworkPane,
    { rootId: partyId },
    { models: ["nexus.Tie", "parties.Relationship", "parties.Party"] },
  );
  const nodes = graphNodes(query.data?.party_graph.nodes);
  const edges = graphEdges(query.data?.party_graph.edges);
  if (query.fetching && !query.data) return <LoadingPanel density="inline" />;
  if (nodes.length <= 1) return <InlineEmpty label={t("network.empty")} />;
  return (
    <GraphView
      className="h-80 rounded-6 border border-border-subtle"
      nodes={nodes}
      edges={edges}
      nodeStyles={nodeStyles}
      edgeStyles={edgeStyles}
      layout={{ rankdir: "LR" }}
      fitViewOptions={{ padding: 0.12 }}
    />
  );
}
