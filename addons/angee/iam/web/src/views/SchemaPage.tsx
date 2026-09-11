import { useAuthoredQuery, type MessageVars } from "@angee/refine";
import { Alert, Badge, Code, GraphView, InlineEmpty, PageAside, PrimaryPanePublisher, RailPanel, SearchInput, Spinner, TreeView, barVariants, cn, routeSearchParam, textRoleVariants, updateRouteSearch, useChatterContent, useRouteSearch, type ChatterTab, type GraphViewEdge, type GraphViewEdgeStyle, type GraphViewNode, type GraphViewNodeStyle } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import { type ReactElement, type ReactNode, useCallback, useEffect, useMemo, useState, } from "react";

import {
  IamRebacSchema,
  type IAMPermissionSchema,
  type IAMRelationSchema,
  type IAMResourceSchema,
} from "../documents";
import { resourceLabel, titleLabel } from "../identity-labels";
import { useIamT } from "../i18n";

type SchemaNodeKind = "resource" | "relation" | "permission";
type SchemaEdgeKind = "contains" | "computed";

interface SchemaNodeMeta extends Record<string, unknown> {
  resource_type: string;
}

type SchemaGraphNode = GraphViewNode<SchemaNodeKind, SchemaNodeMeta>;
type SchemaGraphEdge = GraphViewEdge<SchemaEdgeKind>;

interface SchemaGraph {
  nodes: SchemaGraphNode[];
  edges: SchemaGraphEdge[];
}

const SCHEMA_NODE_STYLES: Record<SchemaNodeKind, GraphViewNodeStyle> = {
  resource: {
    width: 230,
    height: 78,
    type: "input",
    borderColor: "var(--brand)",
    badgeTone: "brand",
  },
  relation: {
    width: 210,
    height: 76,
    borderColor: "var(--border-strong)",
    badgeTone: "info",
  },
  permission: {
    width: 230,
    height: 86,
    type: "output",
    borderColor: "var(--accent)",
    badgeTone: "accent",
  },
};

const SCHEMA_EDGE_STYLES: Record<SchemaEdgeKind, GraphViewEdgeStyle> = {
  contains: {
    stroke: "var(--border-strong)",
    labelColor: "var(--text-muted)",
  },
  computed: {
    stroke: "var(--brand)",
    labelColor: "var(--brand)",
  },
};

/** A translator bound to the `iam` namespace, threaded into non-component helpers. */
type Translate = (key: string, vars?: MessageVars) => string;

export function SchemaPage(): ReactElement {
  const t = useIamT();
  const query = useAuthoredQuery(IamRebacSchema);
  const routeSearch = useRouteSearch();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const resources = useMemo(
    () => normalizeResources(query.data?.rebac_schema ?? []),
    [query.data],
  );
  const visibleResources = useMemo(
    () => resources.filter((resource) => resourceMatches(resource, search)),
    [resources, search],
  );
  const selectedResourceType = routeSearchParam(routeSearch, "resource") ?? "";
  const selectResource = useCallback(
    (resourceType: string, replace = false) => {
      void navigate({
        to: ".",
        search: updateRouteSearch({ resource: resourceType }),
        replace,
      });
    },
    [navigate],
  );

  useEffect(() => {
    if (visibleResources.length === 0) return;
    if (
      !visibleResources.some(
        (resource) => resource.resource_type === selectedResourceType,
      )
    ) {
      selectResource(visibleResources[0]?.resource_type ?? "", true);
    }
  }, [selectResource, selectedResourceType, visibleResources]);

  const selectedResource =
    visibleResources.find(
      (resource) => resource.resource_type === selectedResourceType,
    )
    ?? visibleResources[0]
    ?? null;

  // The query has no data yet (error or first load): the page renders only its
  // own state surface and publishes nothing, so the shell falls back to its own
  // primary/secondary content — mirroring the pre-shell Workbench, which the
  // early returns replaced wholesale.
  const ready = !query.error && !(query.isFetching && resources.length === 0);

  // Primary (left explorer) pane: the resource-type navigator.
  const explorer = useMemo(
    () =>
      ready ? (
        <ResourceTypeList
          resources={visibleResources}
          search={search}
          selectedResource={selectedResource}
          onSearchChange={setSearch}
          onSelect={selectResource}
        />
      ) : null,
    [
      ready,
      visibleResources,
      search,
      selectedResource,
      selectResource,
    ],
  );
  // Secondary (Chatter) pane: an additive "inspector" tab alongside the shell
  // defaults (agent/comments/activity).
  const inspectorTab = useMemo<readonly ChatterTab[]>(
    () => [
      {
        id: "inspector",
        label: t("schema.inspector"),
        icon: "info",
        children: (
          <PageAside collapse="never" gutter="compact" className="h-full w-full border-l-0 bg-sheet-1">
            <RailPanel title={t("schema.inspector")} empty={t("schema.noMatches")}>
              {selectedResource ? <SchemaInspector resource={selectedResource} /> : null}
            </RailPanel>
          </PageAside>
        ),
      },
    ],
    [t, selectedResource],
  );
  const chatter = useMemo(
    () => (ready ? { tabs: inspectorTab } : null),
    [ready, inspectorTab],
  );
  useChatterContent(chatter);

  if (query.error) {
    return (
      <>
        <PrimaryPanePublisher node={explorer} />
        <Alert tone="danger" title={t("schema.unavailable")}>
          {query.error.message}
        </Alert>
      </>
    );
  }

  if (query.isFetching && resources.length === 0) {
    return (
      <>
        <PrimaryPanePublisher node={explorer} />
        <div className={cn(textRoleVariants({ role: "meta" }), "flex items-center gap-2 rounded-6 border border-border-subtle bg-sheet px-4 py-3")}>
          <Spinner size="sm" />
          {t("schema.loading")}
        </div>
      </>
    );
  }

  return (
    <>
      <PrimaryPanePublisher node={explorer} />
      <SchemaGraphCanvas
        resources={visibleResources}
        selectedResource={selectedResource}
        onSelect={selectResource}
      />
    </>
  );
}

function ResourceTypeList({
  resources,
  search,
  selectedResource,
  onSearchChange,
  onSelect,
}: {
  resources: readonly IAMResourceSchema[];
  search: string;
  selectedResource: IAMResourceSchema | null;
  onSearchChange: (value: string) => void;
  onSelect: (resource_type: string) => void;
}): ReactElement {
  const t = useIamT();
  return (
    <section
      role="navigation"
      aria-label={t("schema.resourceTypesLabel")}
      className="flex h-full min-h-0 min-w-0 flex-col"
    >
      <div className="border-b border-border-subtle p-3">
        <SearchInput
          value={search}
          placeholder={t("schema.searchPlaceholder")}
          onChange={(event) => onSearchChange(event.currentTarget.value)}
          onClear={() => onSearchChange("")}
        />
      </div>
      <TreeView<IAMResourceSchema>
        rows={resources}
        rowKey="resource_type"
        label="resource_type"
        selectedId={selectedResource?.resource_type}
        onSelect={(resource) => onSelect(resource.resource_type)}
        emptyContent={t("schema.noMatches")}
        className="min-h-0 flex-1 overflow-auto p-2"
        renderRow={(resource) => (
          <span className="flex min-w-0 flex-1 items-center justify-between gap-3">
            <span className="min-w-0">
              <span className="block truncate text-13 font-medium">
                {resourceLabel(resource.resource_type)}
              </span>
              <Code truncate tone="muted">{resource.resource_type}</Code>
            </span>
            <Badge>{resource.relations.length + resource.permissions.length}</Badge>
          </span>
        )}
      />
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
  onSelect: (resource_type: string) => void;
}): ReactElement {
  const t = useIamT();
  const graph = useMemo(
    () => buildSchemaGraph(resources, selectedResource?.resource_type ?? "", t),
    [resources, selectedResource?.resource_type, t],
  );

  if (resources.length === 0) {
    return (
      <section className={cn(textRoleVariants({ role: "meta" }), "flex h-full min-h-0 p-6")}>
        {t("schema.noMatches")}
      </section>
    );
  }

  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
      <header className={barVariants({ edge: "bottom", pad: "compactTall", align: "start", justify: "between", gap: 3 })}>
        <div className="min-w-0">
          <h2 className={cn("m-0", textRoleVariants({ role: "title", truncate: true }))}>
            {t("schema.permissionGraph")}
          </h2>
          {selectedResource ? (
            <Code className="mt-1" truncate tone="muted">
              {selectedResource.resource_type}
            </Code>
          ) : null}
        </div>
        <Badge tone="info">
          {t("schema.nodeCount", { count: graph.nodes.length })}
        </Badge>
      </header>
      <GraphView
        nodes={graph.nodes}
        edges={graph.edges}
        nodeStyles={SCHEMA_NODE_STYLES}
        edgeStyles={SCHEMA_EDGE_STYLES}
        className="min-h-0 flex-1"
        onNodeClick={(node) => {
          if (node.meta?.resource_type) onSelect(node.meta.resource_type);
        }}
      />
    </section>
  );
}

function SchemaInspector({
  resource,
}: {
  resource: IAMResourceSchema | null;
}): ReactElement {
  const t = useIamT();
  if (!resource) {
    return (
      <section className={cn(textRoleVariants({ role: "meta" }), "flex h-full min-h-0 p-6")}>
        {t("schema.noneSelected")}
      </section>
    );
  }

  return (
    <aside className="flex h-full min-h-0 min-w-0 flex-col">
      <header className={barVariants({ edge: "bottom", pad: "compactTall", align: "start" })}>
        <div className="min-w-0">
          <h2 className={cn("m-0", textRoleVariants({ role: "title", truncate: true }))}>
            {resourceLabel(resource.resource_type)}
          </h2>
          <Code className="mt-1" truncate tone="muted">
            {resource.resource_type}
          </Code>
        </div>
      </header>
      <div className="grid min-h-0 flex-1 content-start gap-5 overflow-auto p-4">
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
  const t = useIamT();
  return (
    <InspectorSection count={relations.length} title={t("schema.relations")}>
      {relations.length > 0 ? (
        relations.map((relation) => (
          <InspectorRow
            key={relation.name}
            code={relation.name}
            title={titleLabel(relation.name)}
          >
            <ChipList
              values={relation.allowed_subject_types}
              empty={t("schema.noSubjects")}
            />
          </InspectorRow>
        ))
      ) : (
        <InlineEmpty label={t("schema.noRelations")} />
      )}
    </InspectorSection>
  );
}

function PermissionList({
  permissions,
}: {
  permissions: readonly IAMPermissionSchema[];
}): ReactElement {
  const t = useIamT();
  return (
    <InspectorSection count={permissions.length} title={t("schema.permissions")}>
      {permissions.length > 0 ? (
        permissions.map((permission) => (
          <InspectorRow
            key={permission.name}
            code={permission.name}
            title={titleLabel(permission.name)}
          >
            <ChipList
              values={permission.conditions.map((condition) => condition.name)}
              empty={t("schema.noConditions")}
            />
          </InspectorRow>
        ))
      ) : (
        <InlineEmpty label={t("schema.noPermissions")} />
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
    <div className="min-w-0 rounded-6 border border-border-subtle bg-sheet-2 p-3">
      <div className="mb-2 min-w-0">
        <div className="truncate text-13 font-medium text-fg">{title}</div>
        <Code truncate tone="muted">
          {code}
        </Code>
      </div>
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
    return <span className={textRoleVariants({ role: "meta" })}>{empty}</span>;
  }
  return (
    <div className="flex min-w-0 flex-wrap gap-1">
      {values.map((value) => (
        <Badge key={value} tone="neutral">
          {value}
        </Badge>
      ))}
    </div>
  );
}

function buildSchemaGraph(
  resources: readonly IAMResourceSchema[],
  selectedResourceType: string,
  t: Translate,
): SchemaGraph {
  const nodes: SchemaGraphNode[] = [];
  const edges: SchemaGraphEdge[] = [];
  const computedEdges = new Map<
    string,
    {
      id: string;
      source: string;
      target: string;
      labels: string[];
    }
  >();

  for (const resource of resources) {
    const resource_id = resourceNodeId(resource.resource_type);
    const relationIds = new Map<string, string>();
    const highlighted = resource.resource_type === selectedResourceType;

    nodes.push(
      schemaNode({
        id: resource_id,
        kind: "resource",
        resource_type: resource.resource_type,
        highlighted,
        title: resourceLabel(resource.resource_type),
        code: resource.resource_type,
        detail: t("schema.resourceDetail", {
          relations: resource.relations.length,
          permissions: resource.permissions.length,
        }),
      }),
    );

    for (const relation of resource.relations) {
      const relationId = relationNodeId(resource.resource_type, relation.name);
      relationIds.set(relation.name, relationId);
      nodes.push(
        schemaNode({
          id: relationId,
          kind: "relation",
          resource_type: resource.resource_type,
          highlighted,
          title: titleLabel(relation.name),
          code: relation.name,
          detail: t("schema.subjectCount", {
            count: relation.allowed_subject_types.length,
          }),
        }),
      );
      edges.push({
        id: `contains:${resource.resource_type}:${relation.name}`,
        source: resource_id,
        target: relationId,
        kind: "contains",
        label: t("schema.edge.contains"),
      });
    }

    for (const permission of resource.permissions) {
      const permissionId = permissionNodeId(
        resource.resource_type,
        permission.name,
      );
      nodes.push(
        schemaNode({
          id: permissionId,
          kind: "permission",
          resource_type: resource.resource_type,
          highlighted,
          title: titleLabel(permission.name),
          code: permission.name,
          detail: t("schema.conditionCount", {
            count: permission.conditions.length,
          }),
        }),
      );

      for (const condition of permission.conditions) {
        const relationName = conditionRelationName(condition.name, relationIds);
        if (!relationName) continue;
        const relationId = relationIds.get(relationName);
        if (!relationId) continue;
        const edgeKey = `${relationId}\u0000${permissionId}`;
        const existingEdge = computedEdges.get(edgeKey);
        if (existingEdge) {
          existingEdge.labels.push(condition.name);
          continue;
        }
        computedEdges.set(edgeKey, {
          id: `computed:${resource.resource_type}:${relationName}:${permission.name}`,
          source: relationId,
          target: permissionId,
          labels: [condition.name],
        });
      }
    }
  }

  for (const edge of computedEdges.values()) {
    edges.push({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      kind: "computed",
      label: mergedConditionLabel(edge.labels),
    });
  }

  return { nodes, edges };
}

function schemaNode({
  id,
  kind,
  resource_type,
  highlighted,
  title,
  code,
  detail,
}: {
  id: string;
  kind: SchemaNodeKind;
  resource_type: string;
  highlighted: boolean;
  title: string;
  code: string;
  detail: ReactNode;
}): SchemaGraphNode {
  return {
    id,
    kind,
    title,
    code,
    detail,
    highlighted,
    meta: {
      resource_type,
    },
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

function mergedConditionLabel(labels: readonly string[]): string {
  return [...new Set(labels)].join(", ");
}

function normalizeResources(
  resources: readonly IAMResourceSchema[],
): IAMResourceSchema[] {
  return [...resources]
    .sort((left, right) => left.resource_type.localeCompare(right.resource_type))
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
    resource.resource_type,
    resourceLabel(resource.resource_type),
    ...resource.relations.flatMap((relation) => [
      relation.name,
      ...relation.allowed_subject_types,
    ]),
    ...resource.permissions.flatMap((permission) => [
      permission.name,
      ...permission.conditions.map((condition) => condition.name),
    ]),
  ].some((value) => value.toLowerCase().includes(term));
}

function resourceNodeId(resource_type: string): string {
  return `resource:${resource_type}`;
}

function relationNodeId(resource_type: string, relation: string): string {
  return `relation:${resource_type}:${relation}`;
}

function permissionNodeId(resource_type: string, permission: string): string {
  return `permission:${resource_type}:${permission}`;
}
