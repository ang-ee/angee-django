import type { DocumentType } from "@angee/gql/console";

import type { WorkflowDefinitionDocument } from "../documents.console";

export type DefinitionSnapshot = DocumentType<typeof WorkflowDefinitionDocument>["workflow_definition"];
export type DefinitionNode = DefinitionSnapshot["nodes"][number] & { clientKey?: string };
export type DefinitionEdge = DefinitionSnapshot["edges"][number] & { clientKey?: string };

export interface WorkflowDefinitionValues extends Record<string, unknown> {
  id: string;
  key: string;
  name: string;
  description: string;
  purpose: string;
  subject_declaration: string;
  status: string;
  version: number;
  lineage_id: string;
  error_workflow: string | null;
  max_steps: number;
  budget: unknown;
  definition: {
    revision: number;
    nodes: Record<string, DefinitionNode>;
    edges: Record<string, DefinitionEdge>;
    readiness: DefinitionSnapshot["readiness"];
  };
}

export function definitionValues(snapshot: DefinitionSnapshot): WorkflowDefinitionValues {
  const workflow = snapshot.workflow;
  return {
    id: workflow.id,
    key: workflow.key,
    name: workflow.name,
    description: workflow.description,
    purpose: String(workflow.purpose),
    subject_declaration: workflow.subject_declaration,
    status: String(workflow.status),
    version: workflow.version,
    lineage_id: workflow.lineage_id,
    error_workflow: workflow.error_workflow?.id ?? null,
    max_steps: workflow.max_steps,
    budget: workflow.budget,
    definition: {
      revision: snapshot.revision,
      nodes: Object.fromEntries(snapshot.nodes.map((node) => [node.id, node])),
      edges: Object.fromEntries(snapshot.edges.map((edge) => [edge.id, edge])),
      readiness: snapshot.readiness,
    },
  };
}

const WORKFLOW_FIELDS = [
  "name", "description", "purpose", "subject_declaration", "error_workflow", "max_steps", "budget",
] as const;
const NODE_FIELDS = ["key", "name", "step_class", "config", "join_rule", "is_entry", "position"] as const;
const EDGE_FIELDS = ["condition"] as const;

export function definitionEdit(
  baseline: WorkflowDefinitionValues,
  current: WorkflowDefinitionValues,
): Record<string, unknown> {
  const workflow = changedFields(baseline, current, WORKFLOW_FIELDS);
  const nodeCreates = [];
  const nodePatches = [];
  const nodeDeletes = [];
  for (const [identity, node] of Object.entries(current.definition.nodes)) {
    const previous = baseline.definition.nodes[identity];
    if (!previous) {
      nodeCreates.push({ client_key: node.clientKey ?? identity, fields: pick(node, NODE_FIELDS) });
    } else {
      const fields = changedFields(previous, node, NODE_FIELDS);
      if (Object.keys(fields).length) nodePatches.push({ id: previous.id, fields });
    }
  }
  for (const [identity, node] of Object.entries(baseline.definition.nodes)) {
    if (!current.definition.nodes[identity]) nodeDeletes.push(node.id);
  }
  const edgeCreates = [];
  const edgePatches = [];
  const edgeDeletes = [];
  for (const [identity, edge] of Object.entries(current.definition.edges)) {
    const previous = baseline.definition.edges[identity];
    const source = endpoint(edge.source, current.definition.nodes);
    const target = endpoint(edge.target, current.definition.nodes);
    if (!previous) {
      edgeCreates.push({
        client_key: edge.clientKey ?? identity,
        source,
        target,
        fields: pick(edge, EDGE_FIELDS),
      });
    } else {
      const fields = changedFields(previous, edge, EDGE_FIELDS);
      const sourceChanged = previous.source !== edge.source;
      const targetChanged = previous.target !== edge.target;
      if (Object.keys(fields).length || sourceChanged || targetChanged) {
        edgePatches.push({
          id: previous.id,
          fields,
          ...(sourceChanged ? { source } : {}),
          ...(targetChanged ? { target } : {}),
        });
      }
    }
  }
  for (const [identity, edge] of Object.entries(baseline.definition.edges)) {
    if (!current.definition.edges[identity]) edgeDeletes.push(edge.id);
  }
  return {
    workflow,
    node_creates: nodeCreates,
    node_patches: nodePatches,
    node_deletes: nodeDeletes,
    edge_creates: edgeCreates,
    edge_patches: edgePatches,
    edge_deletes: edgeDeletes,
  };
}

function endpoint(identity: string, nodes: Record<string, DefinitionNode>): Record<string, string> {
  const node = nodes[identity];
  return node?.id ? { id: node.id } : { client_key: node?.clientKey ?? identity };
}

function changedFields<T extends object>(before: T, after: T, names: readonly string[]): Record<string, unknown> {
  return Object.fromEntries(names.flatMap((name) => {
    const previous = (before as Record<string, unknown>)[name];
    const next = (after as Record<string, unknown>)[name];
    return equal(previous, next) ? [] : [[name, next]];
  }));
}

function pick(value: object, names: readonly string[]): Record<string, unknown> {
  return Object.fromEntries(names.map((name) => [name, (value as Record<string, unknown>)[name]]));
}

export function definitionValueEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(canonicalJson(left)) === JSON.stringify(canonicalJson(right));
}

function canonicalJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalJson);
  if (value !== null && typeof value === "object") return Object.fromEntries(Object.entries(value).sort(([left], [right]) => left.localeCompare(right)).map(([key, child]) => [key, canonicalJson(child)]));
  return value;
}

const equal = definitionValueEqual;
