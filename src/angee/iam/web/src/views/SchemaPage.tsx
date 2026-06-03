import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MutableRefObject,
  type ReactElement,
  type ReactNode,
} from "react";
import * as dagre from "@dagrejs/dagre";
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {
  Alert,
  Badge,
  Button,
  Code,
  SearchInput,
  Spinner,
} from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import {
  IAM_REBAC_SCHEMA_QUERY,
  type IAMPermissionSchema,
  type IAMRebacSchemaData,
  type IAMRelationSchema,
  type IAMResourceSchema,
} from "../documents";
import { resourceLabel, titleLabel } from "../identity-labels";

type SchemaNodeKind = "resource" | "relation" | "permission";
type SchemaEdgeKind = "contains" | "computed";

interface SchemaNodeData extends Record<string, unknown> {
  code: string;
  detail: ReactNode;
  highlighted: boolean;
  kind: SchemaNodeKind;
  label: ReactNode;
  resourceType: string;
}

type SchemaGraphNode = Node<SchemaNodeData>;
type SchemaGraphEdge = Edge<{ kind: SchemaEdgeKind }>;

interface SchemaGraph {
  nodes: SchemaGraphNode[];
  edges: SchemaGraphEdge[];
}

const NODE_SIZE: Record<SchemaNodeKind, { width: number; height: number }> = {
  resource: { width: 230, height: 78 },
  relation: { width: 210, height: 76 },
  permission: { width: 230, height: 86 },
};

export function SchemaPage(): ReactElement {
  const query = useAuthoredQuery<IAMRebacSchemaData>(IAM_REBAC_SCHEMA_QUERY);
  const [search, setSearch] = useState("");
  const resources = useMemo(
    () => normalizeResources(query.data?.rebacSchema ?? []),
    [query.data],
  );
  const visibleResources = useMemo(
    () => resources.filter((resource) => resourceMatches(resource, search)),
    [resources, search],
  );
  const [selectedResourceType, setSelectedResourceType] = useState<string>("");
  const resourceListboxId = useId();
  const optionRefs = useRef(new Map<string, HTMLElement>());

  useEffect(() => {
    if (visibleResources.length === 0) return;
    if (
      !visibleResources.some(
        (resource) => resource.resourceType === selectedResourceType,
      )
    ) {
      setSelectedResourceType(visibleResources[0]?.resourceType ?? "");
    }
  }, [selectedResourceType, visibleResources]);

  if (query.error) {
    return (
      <Alert intent="danger" title="Schema unavailable">
        {query.error.message}
      </Alert>
    );
  }

  if (query.fetching && resources.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-border-subtle bg-sheet px-4 py-3 text-13 text-fg-muted">
        <Spinner size="sm" />
        Loading schema...
      </div>
    );
  }

  const selectedResource =
    visibleResources.find(
      (resource) => resource.resourceType === selectedResourceType,
    )
    ?? visibleResources[0]
    ?? null;
  const selectedIndex = selectedResource
    ? visibleResources.findIndex(
        (resource) => resource.resourceType === selectedResource.resourceType,
      )
    : -1;
  const selectVisibleResource = (index: number, focus = false) => {
    const resource = visibleResources[index];
    if (!resource) return;
    setSelectedResourceType(resource.resourceType);
    if (focus) optionRefs.current.get(resource.resourceType)?.focus();
  };
  const handleResourceListboxKeyDown = (
    event: KeyboardEvent<HTMLDivElement>,
  ) => {
    if (visibleResources.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      selectVisibleResource(
        selectedIndex < 0
          ? 0
          : Math.min(visibleResources.length - 1, selectedIndex + 1),
        true,
      );
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      selectVisibleResource(
        selectedIndex < 0 ? 0 : Math.max(0, selectedIndex - 1),
        true,
      );
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      selectVisibleResource(0, true);
      return;
    }
    if (event.key === "End") {
      event.preventDefault();
      selectVisibleResource(visibleResources.length - 1, true);
    }
  };

  return (
    <div className="grid min-h-[38rem] gap-4 xl:grid-cols-[19rem_minmax(0,1fr)_22rem]">
      <ResourceTypeList
        listboxId={resourceListboxId}
        optionRefs={optionRefs}
        resources={visibleResources}
        search={search}
        selectedResource={selectedResource}
        onKeyDown={handleResourceListboxKeyDown}
        onSearchChange={setSearch}
        onSelect={setSelectedResourceType}
      />
      <SchemaGraphCanvas
        resources={visibleResources}
        selectedResource={selectedResource}
        onSelect={setSelectedResourceType}
      />
      <SchemaInspector resource={selectedResource} />
    </div>
  );
}

function ResourceTypeList({
  listboxId,
  optionRefs,
  resources,
  search,
  selectedResource,
  onKeyDown,
  onSearchChange,
  onSelect,
}: {
  listboxId: string;
  optionRefs: MutableRefObject<Map<string, HTMLElement>>;
  resources: readonly IAMResourceSchema[];
  search: string;
  selectedResource: IAMResourceSchema | null;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
  onSearchChange: (value: string) => void;
  onSelect: (resourceType: string) => void;
}): ReactElement {
  return (
    <section className="min-w-0 rounded-md border border-border-subtle bg-sheet">
      <div className="border-b border-border-subtle p-3">
        <SearchInput
          value={search}
          placeholder="Search schema"
          onChange={(event) => onSearchChange(event.currentTarget.value)}
          onClear={() => onSearchChange("")}
        />
      </div>
      <div
        id={listboxId}
        className="max-h-[34rem] overflow-auto p-2"
        role="listbox"
        aria-label="Resource types"
        aria-activedescendant={
          selectedResource
            ? resourceOptionId(listboxId, selectedResource.resourceType)
            : undefined
        }
        onKeyDown={onKeyDown}
      >
        {resources.length > 0 ? (
          resources.map((resource) => (
            <Button
              key={resource.resourceType}
              ref={(node) => {
                if (node) optionRefs.current.set(resource.resourceType, node);
                else optionRefs.current.delete(resource.resourceType);
              }}
              type="button"
              id={resourceOptionId(listboxId, resource.resourceType)}
              role="option"
              aria-selected={
                resource.resourceType === selectedResource?.resourceType
              }
              tabIndex={
                resource.resourceType === selectedResource?.resourceType
                  ? 0
                  : -1
              }
              variant="ghost"
              className="h-auto w-full min-w-0 justify-between gap-3 whitespace-normal px-3 py-2 text-left data-[selected]:bg-brand-soft data-[selected]:text-brand-soft-text"
              data-selected={
                resource.resourceType === selectedResource?.resourceType
                  ? ""
                  : undefined
              }
              onClick={() => onSelect(resource.resourceType)}
            >
              <span className="min-w-0">
                <span className="block truncate text-13 font-medium">
                  {resourceLabel(resource.resourceType)}
                </span>
                <Code truncate variant="muted">
                  {resource.resourceType}
                </Code>
              </span>
              <Badge>
                {resource.relations.length + resource.permissions.length}
              </Badge>
            </Button>
          ))
        ) : (
          <p className="m-0 px-3 py-6 text-center text-13 text-fg-muted">
            No matching resource types.
          </p>
        )}
      </div>
    </section>
  );
}

function SchemaGraphCanvas({
  resources,
  selectedResource,
  onSelect,
}: {
  resources: readonly IAMResourceSchema[];
  selectedResource: IAMResourceSchema | null;
  onSelect: (resourceType: string) => void;
}): ReactElement {
  const graph = useMemo(
    () => buildSchemaGraph(resources, selectedResource?.resourceType ?? ""),
    [resources, selectedResource?.resourceType],
  );

  if (resources.length === 0) {
    return (
      <section className="min-h-[34rem] rounded-md border border-border-subtle bg-sheet p-6 text-13 text-fg-muted">
        No matching resource types.
      </section>
    );
  }

  return (
    <section className="min-w-0 overflow-hidden rounded-md border border-border-subtle bg-sheet">
      <header className="flex min-w-0 items-start justify-between gap-3 border-b border-border-subtle px-4 py-3">
        <div className="min-w-0">
          <h2 className="m-0 truncate text-sm font-semibold text-fg">
            Permission Graph
          </h2>
          {selectedResource ? (
            <Code className="mt-1" truncate variant="muted">
              {selectedResource.resourceType}
            </Code>
          ) : null}
        </div>
        <Badge variant="info">
          {graph.nodes.length} nodes
        </Badge>
      </header>
      <div className="h-[34rem] min-h-0">
        <ReactFlow
          nodes={graph.nodes}
          edges={graph.edges}
          fitView
          fitViewOptions={{ padding: 0.18 }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          onNodeClick={(_, node) => onSelect(node.data.resourceType)}
        >
          <Background color="var(--border-subtle)" gap={20} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </section>
  );
}

function SchemaInspector({
  resource,
}: {
  resource: IAMResourceSchema | null;
}): ReactElement {
  if (!resource) {
    return (
      <section className="rounded-md border border-border-subtle bg-sheet p-6 text-13 text-fg-muted">
        No resource type selected.
      </section>
    );
  }

  return (
    <aside className="min-w-0 rounded-md border border-border-subtle bg-sheet">
      <header className="border-b border-border-subtle px-4 py-3">
        <h2 className="m-0 truncate text-sm font-semibold text-fg">
          {resourceLabel(resource.resourceType)}
        </h2>
        <Code className="mt-1" truncate variant="muted">
          {resource.resourceType}
        </Code>
      </header>
      <div className="grid gap-5 p-4">
        <RelationList relations={resource.relations} />
        <PermissionList permissions={resource.permissions} />
      </div>
    </aside>
  );
}

function RelationList({
  relations,
}: {
  relations: readonly IAMRelationSchema[];
}): ReactElement {
  return (
    <InspectorSection count={relations.length} title="Relations">
      {relations.length > 0 ? (
        relations.map((relation) => (
          <InspectorRow
            key={relation.name}
            code={relation.name}
            title={titleLabel(relation.name)}
          >
            <ChipList
              values={relation.allowedSubjectTypes}
              empty="No subjects"
            />
          </InspectorRow>
        ))
      ) : (
        <EmptyInspectorRow>No relations.</EmptyInspectorRow>
      )}
    </InspectorSection>
  );
}

function PermissionList({
  permissions,
}: {
  permissions: readonly IAMPermissionSchema[];
}): ReactElement {
  return (
    <InspectorSection count={permissions.length} title="Permissions">
      {permissions.length > 0 ? (
        permissions.map((permission) => (
          <InspectorRow
            key={permission.name}
            code={permission.name}
            title={titleLabel(permission.name)}
          >
            <ChipList
              values={permission.conditions.map((condition) => condition.name)}
              empty="No conditions"
            />
          </InspectorRow>
        ))
      ) : (
        <EmptyInspectorRow>No permissions.</EmptyInspectorRow>
      )}
    </InspectorSection>
  );
}

function InspectorSection({
  children,
  count,
  title,
}: {
  children: ReactNode;
  count: number;
  title: string;
}): ReactElement {
  return (
    <section className="min-w-0">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="m-0 text-13 font-semibold text-fg">{title}</h3>
        <Badge>{count}</Badge>
      </div>
      <div className="grid gap-2">{children}</div>
    </section>
  );
}

function InspectorRow({
  children,
  code,
  title,
}: {
  children: ReactNode;
  code: string;
  title: string;
}): ReactElement {
  return (
    <div className="min-w-0 rounded-md border border-border-subtle bg-sheet-2 p-3">
      <div className="mb-2 min-w-0">
        <div className="truncate text-13 font-medium text-fg">{title}</div>
        <Code truncate variant="muted">
          {code}
        </Code>
      </div>
      {children}
    </div>
  );
}

function EmptyInspectorRow({
  children,
}: {
  children: ReactNode;
}): ReactElement {
  return (
    <div className="rounded-md border border-dashed border-border-subtle bg-inset px-3 py-4 text-center text-13 text-fg-muted">
      {children}
    </div>
  );
}

function ChipList({
  values,
  empty,
}: {
  values: readonly string[];
  empty: string;
}): ReactElement {
  if (values.length === 0) {
    return <span className="text-13 text-fg-muted">{empty}</span>;
  }
  return (
    <div className="flex min-w-0 flex-wrap gap-1">
      {values.map((value) => (
        <Badge key={value} variant="default">
          {value}
        </Badge>
      ))}
    </div>
  );
}

function buildSchemaGraph(
  resources: readonly IAMResourceSchema[],
  selectedResourceType: string,
): SchemaGraph {
  const nodes: SchemaGraphNode[] = [];
  const edges: SchemaGraphEdge[] = [];

  for (const resource of resources) {
    const resourceId = resourceNodeId(resource.resourceType);
    const relationIds = new Map<string, string>();
    const highlighted = resource.resourceType === selectedResourceType;

    nodes.push(
      schemaNode({
        id: resourceId,
        kind: "resource",
        resourceType: resource.resourceType,
        highlighted,
        title: resourceLabel(resource.resourceType),
        code: resource.resourceType,
        detail: `${resource.relations.length} relations / ${resource.permissions.length} permissions`,
      }),
    );

    for (const relation of resource.relations) {
      const relationId = relationNodeId(resource.resourceType, relation.name);
      relationIds.set(relation.name, relationId);
      nodes.push(
        schemaNode({
          id: relationId,
          kind: "relation",
          resourceType: resource.resourceType,
          highlighted,
          title: titleLabel(relation.name),
          code: relation.name,
          detail:
            relation.allowedSubjectTypes.length === 1
              ? "1 subject"
              : `${relation.allowedSubjectTypes.length} subjects`,
        }),
      );
      edges.push({
        id: `contains:${resource.resourceType}:${relation.name}`,
        source: resourceId,
        target: relationId,
        type: "smoothstep",
        data: { kind: "contains" },
        label: "contains",
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { stroke: "var(--border-strong)" },
        labelStyle: { fill: "var(--text-muted)", fontSize: 11 },
      });
    }

    for (const permission of resource.permissions) {
      const permissionId = permissionNodeId(
        resource.resourceType,
        permission.name,
      );
      nodes.push(
        schemaNode({
          id: permissionId,
          kind: "permission",
          resourceType: resource.resourceType,
          highlighted,
          title: titleLabel(permission.name),
          code: permission.name,
          detail:
            permission.conditions.length === 1
              ? "1 condition"
              : `${permission.conditions.length} conditions`,
        }),
      );

      for (const condition of permission.conditions) {
        const relationName = conditionRelationName(condition.name, relationIds);
        if (!relationName) continue;
        const relationId = relationIds.get(relationName);
        if (!relationId) continue;
        edges.push({
          id: `computed:${resource.resourceType}:${relationName}:${permission.name}:${condition.name}`,
          source: relationId,
          target: permissionId,
          type: "smoothstep",
          data: { kind: "computed" },
          label: condition.name,
          markerEnd: { type: MarkerType.ArrowClosed },
          style: { stroke: "var(--brand)" },
          labelStyle: { fill: "var(--brand)", fontSize: 11 },
        });
      }
    }
  }

  return layoutGraph(nodes, edges);
}

function schemaNode({
  id,
  kind,
  resourceType,
  highlighted,
  title,
  code,
  detail,
}: {
  id: string;
  kind: SchemaNodeKind;
  resourceType: string;
  highlighted: boolean;
  title: string;
  code: string;
  detail: ReactNode;
}): SchemaGraphNode {
  const size = NODE_SIZE[kind];
  return {
    id,
    type: nodeType(kind),
    position: { x: 0, y: 0 },
    sourcePosition: Position.Bottom,
    targetPosition: Position.Top,
    data: {
      code,
      detail,
      highlighted,
      kind,
      resourceType,
      label: (
        <GraphNodeLabel
          code={code}
          detail={detail}
          kind={kind}
          title={title}
        />
      ),
    },
    style: {
      width: size.width,
      minHeight: size.height,
      borderColor: highlighted ? "var(--brand)" : nodeBorder(kind),
      borderWidth: highlighted ? 2 : 1,
      background: highlighted
        ? "var(--brand-soft)"
        : "var(--surface-sheet)",
      color: "var(--text-primary)",
      padding: 0,
    },
  };
}

function GraphNodeLabel({
  code,
  detail,
  kind,
  title,
}: {
  code: string;
  detail: ReactNode;
  kind: SchemaNodeKind;
  title: string;
}): ReactElement {
  return (
    <div className="min-w-0 px-3 py-2 text-left">
      <div className="mb-1 flex min-w-0 items-center justify-between gap-2">
        <span className="truncate text-13 font-semibold text-fg">{title}</span>
        <Badge density="compact" variant={nodeBadgeVariant(kind)}>
          {kind}
        </Badge>
      </div>
      <Code truncate variant="muted">
        {code}
      </Code>
      <div className="mt-1 truncate text-2xs text-fg-muted">{detail}</div>
    </div>
  );
}

function layoutGraph(
  nodes: readonly SchemaGraphNode[],
  edges: readonly SchemaGraphEdge[],
): SchemaGraph {
  const graph = new dagre.graphlib.Graph({ directed: true, multigraph: true });
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: "TB",
    nodesep: 34,
    ranksep: 76,
    edgesep: 18,
    marginx: 24,
    marginy: 24,
  });

  for (const node of nodes) {
    const kind = node.data.kind;
    graph.setNode(node.id, NODE_SIZE[kind]);
  }
  for (const edge of edges) {
    graph.setEdge(edge.source, edge.target, { weight: 1 }, edge.id);
  }

  dagre.layout(graph);

  return {
    nodes: nodes.map((node) => {
      const position = graph.node(node.id);
      const kind = node.data.kind;
      const size = NODE_SIZE[kind];
      return {
        ...node,
        position: {
          x: position.x - size.width / 2,
          y: position.y - size.height / 2,
        },
      };
    }),
    edges: [...edges],
  };
}

function conditionRelationName(
  conditionName: string,
  relationIds: ReadonlyMap<string, string>,
): string | null {
  if (relationIds.has(conditionName)) return conditionName;
  const arrowIndex = conditionName.indexOf("->");
  if (arrowIndex < 0) return null;
  const viaRelation = conditionName.slice(0, arrowIndex);
  return relationIds.has(viaRelation) ? viaRelation : null;
}

function nodeType(kind: SchemaNodeKind): "default" | "input" | "output" {
  if (kind === "resource") return "input";
  if (kind === "permission") return "output";
  return "default";
}

function nodeBorder(kind: SchemaNodeKind): string {
  if (kind === "resource") return "var(--brand)";
  if (kind === "permission") return "var(--accent)";
  return "var(--border-strong)";
}

function nodeBadgeVariant(
  kind: SchemaNodeKind,
): "accent" | "brand" | "info" {
  if (kind === "resource") return "brand";
  if (kind === "permission") return "accent";
  return "info";
}

function normalizeResources(
  resources: readonly IAMResourceSchema[],
): IAMResourceSchema[] {
  return [...resources]
    .sort((left, right) => left.resourceType.localeCompare(right.resourceType))
    .map((resource) => ({
      ...resource,
      relations: [...resource.relations].sort((left, right) =>
        left.name.localeCompare(right.name),
      ),
      permissions: [...resource.permissions]
        .sort((left, right) => left.name.localeCompare(right.name))
        .map((permission) => ({
          ...permission,
          conditions: [...permission.conditions].sort((left, right) =>
            left.name.localeCompare(right.name),
          ),
        })),
    }));
}

function resourceMatches(resource: IAMResourceSchema, search: string): boolean {
  const term = search.trim().toLowerCase();
  if (!term) return true;
  return [
    resource.resourceType,
    resourceLabel(resource.resourceType),
    ...resource.relations.flatMap((relation) => [
      relation.name,
      ...relation.allowedSubjectTypes,
    ]),
    ...resource.permissions.flatMap((permission) => [
      permission.name,
      ...permission.conditions.map((condition) => condition.name),
    ]),
  ].some((value) => value.toLowerCase().includes(term));
}

function resourceNodeId(resourceType: string): string {
  return `resource:${resourceType}`;
}

function relationNodeId(resourceType: string, relation: string): string {
  return `relation:${resourceType}:${relation}`;
}

function permissionNodeId(resourceType: string, permission: string): string {
  return `permission:${resourceType}:${permission}`;
}

function resourceOptionId(listboxId: string, resourceType: string): string {
  return `${listboxId}-${resourceType.replace(/[^a-zA-Z0-9_-]+/g, "-")}`;
}
