import { useAuthoredQuery, type MessageVars } from "@angee/refine";
import { Code, ErrorBanner, GraphView, InlineEmpty, LoadingPanel, OverflowChipList, PaneLabel, PaneRow, PaneRows, PaneToolbar, PaneToolbarHost, PrimaryPanePublisher, RecordHeader, SearchInput, TreeView, routeSearchParam, titleCase, updateRouteSearch, useChatterContent, useRouteSearch, type ChatterTab, type GraphViewEdge, type GraphViewEdgeStyle, type GraphViewNode, type GraphViewNodeStyle } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import { type ReactElement, useCallback, useEffect, useMemo, useState } from "react";

import { IamRebacSchema, type IAMResourceSchema } from "../documents";
import { resourceLabel } from "../identity-labels";
import { useIamT } from "../i18n";

type SchemaNodeKind = "resource" | "relation" | "permission";
type SchemaEdgeKind = "contains" | "computed";

type SchemaGraphNode = GraphViewNode<SchemaNodeKind>;
type SchemaGraphEdge = GraphViewEdge<SchemaEdgeKind>;

interface SchemaGraph {
  nodes: SchemaGraphNode[];
  edges: SchemaGraphEdge[];
}

/** A resource type as a navigator row: its schema plus the member count the tree shows. */
type ResourceTypeRow = IAMResourceSchema & { label: string; count: number };

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

/**
 * The REBAC schema explorer: the resource-type navigator in the primary pane,
 * the selected type's permission graph as the subject, and its relations and
 * permissions as the aside's Inspector tab.
 */
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
    if (!visibleResources.some((resource) => resource.resource_type === selectedResourceType)) {
      selectResource(visibleResources[0]?.resource_type ?? "", true);
    }
  }, [selectResource, selectedResourceType, visibleResources]);

  const selectedResource =
    visibleResources.find((resource) => resource.resource_type === selectedResourceType)
    ?? visibleResources[0]
    ?? null;

  // The query has no data yet (error or first load): the page renders only its
  // own state surface and publishes nothing, so the shell falls back to its own
  // primary/secondary content.
  const ready = !query.error && !(query.isFetching && resources.length === 0);

  const explorer = useMemo(
    () => ready ? (
      <ResourceTypeNavigator
        resources={visibleResources}
        search={search}
        selectedResource={selectedResource}
        onSearchChange={setSearch}
        onSelect={selectResource}
      />
    ) : null,
    [ready, visibleResources, search, selectedResource, selectResource],
  );
  // The Inspector: an additive aside tab beside the shell defaults.
  const inspectorTab = useMemo<readonly ChatterTab[]>(
    () => [{
      id: "inspector",
      label: t("schema.inspector"),
      icon: "info",
      children: selectedResource
        ? <SchemaInspector resource={selectedResource} />
        : <InlineEmpty label={t("schema.noMatches")} />,
    }],
    [t, selectedResource],
  );
  const chatter = useMemo(() => (ready ? { tabs: inspectorTab } : null), [ready, inspectorTab]);
  useChatterContent(chatter);

  if (query.error) {
    return (
      <>
        <PrimaryPanePublisher node={explorer} />
        <ErrorBanner title={t("schema.unavailable")} description={query.error.message} />
      </>
    );
  }

  if (query.isFetching && resources.length === 0) {
    return (
      <>
        <PrimaryPanePublisher node={explorer} />
        <LoadingPanel density="inline" message={t("schema.loading")} />
      </>
    );
  }

  return (
    <>
      <PrimaryPanePublisher node={explorer} />
      <SchemaSubject resource={selectedResource} />
    </>
  );
}

/** The navigator pane: a filter in the pane toolbar over the resource-type tree. */
function ResourceTypeNavigator({
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
  const rows = useMemo<readonly ResourceTypeRow[]>(
    () => resources.map((resource) => ({
      ...resource,
      label: resourceLabel(resource.resource_type),
      count: resource.relations.length + resource.permissions.length,
    })),
    [resources],
  );
  return (
    <PaneToolbarHost role="navigation" aria-label={t("schema.resourceTypesLabel")}>
      <PaneToolbar
        search={
          <SearchInput
            value={search}
            placeholder={t("schema.searchPlaceholder")}
            onChange={(event) => onSearchChange(event.currentTarget.value)}
            onClear={() => onSearchChange("")}
          />
        }
      />
      <TreeView<ResourceTypeRow>
        rows={rows}
        rowKey="resource_type"
        label="label"
        badge="count"
        selectedId={selectedResource?.resource_type}
        onSelect={(resource) => onSelect(resource.resource_type)}
        emptyContent={t("schema.noMatches")}
        className="min-h-0 flex-1 overflow-auto p-2"
      />
    </PaneToolbarHost>
  );
}

/**
 * The subject: the selected resource type's permission graph under its record
 * header. Every schema edge stays inside one resource type, so each type is its
 * own graph; the navigator chooses which one to lay out.
 */
function SchemaSubject({ resource }: { resource: IAMResourceSchema | null }): ReactElement {
  const t = useIamT();
  const graph = useMemo(() => (resource ? buildSchemaGraph(resource, t) : null), [resource, t]);

  if (!resource || !graph) {
    return <InlineEmpty label={t("schema.noMatches")} className="p-6" />;
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
      <RecordHeader
        density="compact"
        crumbs={t("schema.permissionGraph")}
        title={resourceLabel(resource.resource_type)}
        meta={<><Code truncate tone="muted">{resource.resource_type}</Code><span>{t("schema.nodeCount", { count: graph.nodes.length })}</span></>}
      />
      <GraphView
        nodes={graph.nodes}
        edges={graph.edges}
        nodeStyles={SCHEMA_NODE_STYLES}
        edgeStyles={SCHEMA_EDGE_STYLES}
        className="min-h-0 flex-1"
      />
    </div>
  );
}

/** The Inspector pane: the selected type's relations and permissions as pane rows. */
function SchemaInspector({ resource }: { resource: IAMResourceSchema }): ReactElement {
  const t = useIamT();
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
      <PaneRows>
        <PaneLabel>{resourceLabel(resource.resource_type)}</PaneLabel>
        <PaneLabel count={resource.relations.length}>{t("schema.relations")}</PaneLabel>
        {resource.relations.length > 0 ? resource.relations.map((relation) => (
          <PaneRow
            key={relation.name}
            icon="link"
            title={titleCase(relation.name)}
            detail={<Code truncate tone="muted">{relation.name}</Code>}
            sub={<SchemaChips values={relation.allowed_subject_types} empty={t("schema.noSubjects")} />}
          />
        )) : <InlineEmpty label={t("schema.noRelations")} />}
        <PaneLabel count={resource.permissions.length}>{t("schema.permissions")}</PaneLabel>
        {resource.permissions.length > 0 ? resource.permissions.map((permission) => (
          <PaneRow
            key={permission.name}
            icon="auth"
            title={titleCase(permission.name)}
            detail={<Code truncate tone="muted">{permission.name}</Code>}
            sub={<SchemaChips values={permission.conditions.map((condition) => condition.name)} empty={t("schema.noConditions")} />}
          />
        )) : <InlineEmpty label={t("schema.noPermissions")} />}
      </PaneRows>
    </div>
  );
}

function SchemaChips({ values, empty }: { values: readonly string[]; empty: string }): ReactElement {
  if (values.length === 0) return <>{empty}</>;
  return <OverflowChipList items={values.map((value) => ({ id: value, label: value }))} />;
}

function buildSchemaGraph(
  resource: IAMResourceSchema,
  t: Translate,
): SchemaGraph {
  const resource_id = resourceNodeId(resource.resource_type);
  const nodes: SchemaGraphNode[] = [{
    id: resource_id,
    kind: "resource",
    title: resourceLabel(resource.resource_type),
    code: resource.resource_type,
    detail: t("schema.resourceDetail", {
      relations: resource.relations.length,
      permissions: resource.permissions.length,
    }),
  }];
  const edges: SchemaGraphEdge[] = [];
  const relationIds = new Map<string, string>();
  const computedEdges = new Map<
    string,
    {
      id: string;
      source: string;
      target: string;
      labels: string[];
    }
  >();

  for (const relation of resource.relations) {
    const relationId = relationNodeId(resource.resource_type, relation.name);
    relationIds.set(relation.name, relationId);
    nodes.push({
      id: relationId,
      kind: "relation",
      title: titleCase(relation.name),
      code: relation.name,
      detail: t("schema.subjectCount", {
        count: relation.allowed_subject_types.length,
      }),
    });
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
    nodes.push({
      id: permissionId,
      kind: "permission",
      title: titleCase(permission.name),
      code: permission.name,
      detail: t("schema.conditionCount", {
        count: permission.conditions.length,
      }),
    });

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
