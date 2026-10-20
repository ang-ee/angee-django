import { createNamespaceT } from "@angee/ui";
import { inboxMessages } from "./inbox/copy";

export const enNexusMessages: Record<string, string> = {
  ...inboxMessages,
  "ties.partyA": "Party A",
  "ties.partyB": "Party B",
  "ties.aToB": "A → B",
  "ties.bToA": "B → A",
  "ties.gravity": "Gravity",
  "ties.messages": "Messages",
  "ties.lastContact": "Last contact",
  "ties.fading": "Fading",
  "ties.group.pair": "Pair",
  "ties.group.analytics": "Analytics",
  "cadences.party": "Party",
  "cadences.days": "Stay in touch (days)",
  "cadences.touchDue": "Touch due",
  "cadences.group.schedule": "Schedule",
  "network.label": "Network",
  "network.empty": "No actor-visible network neighbors yet.",
  "graph.title": "Party graph",
  "graph.description": "Explore relationship, circle, and identity connections.",
  "graph.root": "Graph root",
  "graph.searchRoots": "Search people, organizations, or circles…",
  "graph.circlePrefix": "Circle",
  "graph.lens": "Graph lens",
  "graph.lens.ego": "Ego",
  "graph.lens.circle": "Circle",
  "graph.lens.identity": "Identity",
  "graph.empty.title": "No graph roots",
  "graph.empty.description": "Add a party or circle to start exploring.",
  "graph.noResults": "No connections in this lens.",
  "graph.truncated": "Showing a bounded neighborhood",
  "graph.inspector": "Inspector",
  "graph.rootsUnavailable": "The graph roots could not be loaded",
  "graph.node.root": "Root",
  "graph.node.party": "Party",
  "graph.node.circle": "Circle",
  "graph.node.identity": "Identity",
  "graph.node.relationshipTarget": "Related party",
  "graph.edge.tieWeak": "Weak tie",
  "graph.edge.tieMedium": "Tie",
  "graph.edge.tieStrong": "Strong tie",
  "graph.edge.tieFading": "Fading tie",
  "graph.edge.relationship": "Relationship",
  "graph.edge.membership": "Membership",
  "graph.edge.identity": "Identity",
  "graph.inspector.empty": "Select nodes or an edge to inspect them.",
  "graph.cadenceDays": "Every {count} days",
  "graph.openRecord": "Open record",
  "graph.openTimeline": "Open timeline",
  "graph.merge": "Compare and merge",
  "graph.mergeHint": "Select exactly two parties to compare and merge them.",
  "overview.fading.metric": "Falling out of touch",
  "overview.fading.title": "Falling out of touch",
  "overview.fading.empty": "No viewer relationships are fading.",
  "overview.due.metric": "Due today",
  "overview.due.title": "Cadences due today",
  "overview.due.empty": "No stay-in-touch cadences are due today.",
};

export const useNexusT = createNamespaceT("nexus", enNexusMessages);
export type NexusT = ReturnType<typeof useNexusT>;

/** A server token's label (`inbox.<kind>.<token>`), or nothing when no copy names it: a token never reads as a key path. */
export function connectionLabel(t: NexusT, kind: "relation" | "role", value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  const key = `inbox.${kind}.${value}`;
  return key in enNexusMessages ? t(key) : undefined;
}
