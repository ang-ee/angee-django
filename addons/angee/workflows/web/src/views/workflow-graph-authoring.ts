import type { GraphViewGeometry, GraphViewPosition } from "@angee/ui";

import { workflowNodeKind, type WorkflowGraphNodeKind } from "./graph-data";
import type { DefinitionEdge, DefinitionNode } from "./workflow-definition-state";

export type WorkflowOperationPlacement = { kind: "first" | "after" | "insert"; identity?: string };

export function graphWithOperation(node: DefinitionNode, placement: WorkflowOperationPlacement, nodes: Record<string, DefinitionNode>, edges: Record<string, DefinitionEdge>, geometry?: GraphViewGeometry<WorkflowGraphNodeKind> | null): { nodes: Record<string, DefinitionNode>; edges: Record<string, DefinitionEdge> } | null {
  const identity = node.clientKey || node.id;
  const stableNodes = materializedPositions(nodes, geometry);
  let nextNodes = { ...stableNodes, [identity]: placedOperationNode(node, placement, edges, geometry) };
  if (placement.kind === "after" && placement.identity) return { nodes: nextNodes, edges: { ...edges, ...newEdge(placement.identity, identity) } };
  if (placement.kind === "insert" && placement.identity) {
    const replaced = edges[placement.identity];
    if (!replaced) return null;
    nextNodes = makeInsertionRoom(identity, replaced, nextNodes, edges, geometry);
    const { [placement.identity]: _removed, ...remaining } = edges;
    return { nodes: nextNodes, edges: { ...remaining, ...newEdge(replaced.source, identity, replaced.condition), ...newEdge(identity, replaced.target) } };
  }
  return { nodes: nextNodes, edges };
}

export function graphWithDuplicate(node: DefinitionNode, sourceIdentity: string, nodes: Record<string, DefinitionNode>, geometry?: GraphViewGeometry<WorkflowGraphNodeKind> | null): Record<string, DefinitionNode> {
  const sourceBounds = geometry?.nodeBounds(sourceIdentity);
  const identity = node.clientKey || node.id;
  if (!sourceBounds || !geometry) return { ...nodes, [identity]: node };
  const stableNodes = materializedPositions(nodes, geometry);
  const layout = geometry.layout();
  const position = geometry.firstFreePosition(workflowNodeKind(node.step_class), {
    x: sourceBounds.x + sourceBounds.width + layout.nodesep,
    y: sourceBounds.y,
  }, "horizontal", new Set([sourceIdentity]));
  return { ...stableNodes, [identity]: { ...node, position } };
}

function newEdge(source: string, target: string, condition = ""): Record<string, DefinitionEdge> {
  const clientKey = `edge-${crypto.randomUUID()}`;
  return { [clientKey]: { id: "", clientKey, source, target, condition } as DefinitionEdge };
}

function placedOperationNode(node: DefinitionNode, placement: WorkflowOperationPlacement, edges: Record<string, DefinitionEdge>, geometry?: GraphViewGeometry<WorkflowGraphNodeKind> | null): DefinitionNode {
  if (!geometry || !placement.identity || placement.kind === "first") return node;
  const sourceIdentity = placement.kind === "after" ? placement.identity : edges[placement.identity]?.source;
  const sourceBounds = sourceIdentity ? geometry.nodeBounds(sourceIdentity) : undefined;
  if (!sourceBounds) return node;
  const preferred = { x: sourceBounds.x, y: sourceBounds.y + sourceBounds.height + geometry.layout().ranksep };
  const position = placement.kind === "after"
    ? geometry.firstFreePosition(workflowNodeKind(node.step_class), preferred, "horizontal", new Set([sourceIdentity!]))
    : preferred;
  return { ...node, position };
}

function makeInsertionRoom(identity: string, replaced: DefinitionEdge, nodes: Record<string, DefinitionNode>, edges: Record<string, DefinitionEdge>, geometry?: GraphViewGeometry<WorkflowGraphNodeKind> | null): Record<string, DefinitionNode> {
  if (!geometry) return nodes;
  const inserted = nodes[identity];
  const insertedPosition = inserted ? positionFrom(inserted.position) : undefined;
  const targetBounds = geometry.nodeBounds(replaced.target);
  if (!inserted || !insertedPosition || !targetBounds) return nodes;
  const insertedSize = geometry.nodeSize(workflowNodeKind(inserted.step_class));
  const shift = Math.max(0, insertedPosition.y + insertedSize.height + geometry.layout().ranksep - targetBounds.y);
  const suffix = downstreamNodes(replaced.target, edges);
  if (suffix.has(replaced.source)) return withSafeLane(identity, replaced.source, inserted, nodes, geometry);
  if ([...suffix].some((nodeIdentity) => {
    const bounds = geometry.nodeBounds(nodeIdentity);
    return bounds != null && bounds.y < targetBounds.y;
  })) return withSafeLane(identity, replaced.source, inserted, nodes, geometry);
  const shiftedBounds = [...suffix].flatMap((nodeIdentity) => {
    const bounds = geometry.nodeBounds(nodeIdentity);
    return bounds ? [{ ...bounds, y: bounds.y + shift }] : [];
  });
  const insertedClear = !geometry.intersects({ ...insertedPosition, ...insertedSize }, new Set([...suffix, replaced.source]));
  if (shift === 0 && insertedClear) return nodes;
  const suffixClear = shiftedBounds.every((bounds) => !geometry.intersects(bounds, suffix));
  if (!insertedClear || !suffixClear) return withSafeLane(identity, replaced.source, inserted, nodes, geometry);
  return Object.fromEntries(Object.entries(nodes).map(([nodeIdentity, current]) => {
    if (!suffix.has(nodeIdentity)) return [nodeIdentity, current];
    const bounds = geometry.nodeBounds(nodeIdentity);
    return [nodeIdentity, bounds ? { ...current, position: { x: bounds.x, y: bounds.y + shift } } : current];
  }));
}

function withSafeLane(identity: string, source: string, inserted: DefinitionNode, nodes: Record<string, DefinitionNode>, geometry: GraphViewGeometry<WorkflowGraphNodeKind>): Record<string, DefinitionNode> {
  const preferred = positionFrom(inserted.position) ?? { x: 0, y: 0 };
  const position = geometry.firstFreePosition(workflowNodeKind(inserted.step_class), preferred, "horizontal", new Set([source]));
  return { ...nodes, [identity]: { ...inserted, position } };
}

function downstreamNodes(start: string, edges: Record<string, DefinitionEdge>): Set<string> {
  const reached = new Set<string>();
  const pending = [start];
  while (pending.length) {
    const identity = pending.pop()!;
    if (reached.has(identity)) continue;
    reached.add(identity);
    for (const edge of Object.values(edges)) if (edge.source === identity && !reached.has(edge.target)) pending.push(edge.target);
  }
  return reached;
}

function materializedPositions(nodes: Record<string, DefinitionNode>, geometry?: GraphViewGeometry<WorkflowGraphNodeKind> | null): Record<string, DefinitionNode> {
  if (!geometry) return nodes;
  return Object.fromEntries(Object.entries(nodes).map(([identity, node]) => {
    if (positionFrom(node.position)) return [identity, node];
    const bounds = geometry.nodeBounds(identity);
    return [identity, bounds ? { ...node, position: { x: bounds.x, y: bounds.y } } : node];
  }));
}

export function positionFrom(value: unknown): GraphViewPosition | undefined {
  if (!value || typeof value !== "object") return undefined;
  const position = value as { x?: unknown; y?: unknown };
  return typeof position.x === "number" && typeof position.y === "number" ? { x: position.x, y: position.y } : undefined;
}

export function graphWithMapBody(
  body: DefinitionNode,
  ownerIdentity: string,
  nodes: Record<string, DefinitionNode>,
  edges: Record<string, DefinitionEdge>,
  geometry: GraphViewGeometry<WorkflowGraphNodeKind> | null,
): { nodes: Record<string, DefinitionNode>; edges: Record<string, DefinitionEdge> } | null {
  const owner = nodes[ownerIdentity];
  if (!owner) return null;
  const identity = body.clientKey || body.id;
  const placed = graphWithOperation(body, { kind: "after", identity: ownerIdentity }, nodes, edges, geometry);
  if (!placed) return null;
  return {
    nodes: {
      ...placed.nodes,
      [ownerIdentity]: {
        ...owner,
        config: { ...(owner.config ?? {}), target_step: placed.nodes[identity]?.key ?? body.key },
      },
    },
    edges,
  };
}
