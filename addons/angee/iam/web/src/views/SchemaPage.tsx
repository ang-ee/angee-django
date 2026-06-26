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

import {
  Alert,
  Badge,
  Button,
  Code,
  GraphView,
  SearchInput,
  Spinner,
  type GraphViewEdge,
  type GraphViewEdgeStyle,
  type GraphViewNode,
  type GraphViewNodeStyle,
  useAuthoredQuery,
} from "@angee/ui";
import { type MessageVars } from "@angee/refine";

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
  const [search, setSearch] = useState("");
  const resources = useMemo(
    () => normalizeResources(query.data?.rebac_schema ?? []),
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
        (resource) => resource.resource_type === selectedResourceType,
      )
    ) {
      setSelectedResourceType(visibleResources[0]?.resource_type ?? "");
    }
  }, [selectedResourceType, visibleResources]);

  if (query.error) {
    return (
      <Alert tone="danger" title={t("iam.schema.unavailable")}>
        {query.error.message}
      </Alert>
    );
  }

  if (query.fetching && resources.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-border-subtle bg-sheet px-4 py-3 text-13 text-fg-muted">
        <Spinner size="sm" />
        {t("iam.schema.loading")}
      </div>
    );
  }

  const selectedResource =
    visibleResources.find(
      (resource) => resource.resource_type === selectedResourceType,
    )
    ?? visibleResources[0]
    ?? null;
  const selectedIndex = selectedResource
    ? visibleResources.findIndex(
        (resource) => resource.resource_type === selectedResource.resource_type,
      )
    : -1;
  const selectVisibleResource = (index: number, focus = false) => {
    const resource = visibleResources[index];
    if (!resource) return;
    setSelectedResourceType(resource.resource_type);
    if (focus) optionRefs.current.get(resource.resource_type)?.focus();
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
  onSelect: (resource_type: string) => void;
}): ReactElement {
  const t = useIamT();
  return (
    <section className="min-w-0 rounded-md border border-border-subtle bg-sheet">
      <div className="border-b border-border-subtle p-3">
        <SearchInput
          value={search}
          placeholder={t("iam.schema.searchPlaceholder")}
          onChange={(event) => onSearchChange(event.currentTarget.value)}
          onClear={() => onSearchChange("")}
        />
      </div>
      <div
        id={listboxId}
        className="max-h-[34rem] overflow-auto p-2"
        role="listbox"
        aria-label={t("iam.schema.resourceTypesLabel")}
        onKeyDown={onKeyDown}
      >
        {resources.length > 0 ? (
          resources.map((resource) => (
            <Button
              key={resource.resource_type}
              ref={(node) => {
                if (node) optionRefs.current.set(resource.resource_type, node);
                else optionRefs.current.delete(resource.resource_type);
              }}
              type="button"
              id={resourceOptionId(listboxId, resource.resource_type)}
              role="option"
              aria-selected={
                resource.resource_type === selectedResource?.resource_type
              }
              tabIndex={
                resource.resource_type === selectedResource?.resource_type
                  ? 0
                  : -1
              }
              variant="ghost"
              className="h-auto w-full min-w-0 justify-between gap-3 whitespace-normal px-3 py-2 text-left data-[selected]:bg-brand-soft data-[selected]:text-brand-soft-text"
              data-selected={
                resource.resource_type === selectedResource?.resource_type
                  ? ""
                  : undefined
              }
              onClick={() => onSelect(resource.resource_type)}
            >
              <span className="min-w-0">
                <span className="block truncate text-13 font-medium">
                  {resourceLabel(resource.resource_type)}
                </span>
                <Code truncate tone="muted">
                  {resource.resource_type}
                </Code>
              </span>
              <Badge>
                {resource.relations.length + resource.permissions.length}
              </Badge>
            </Button>
          ))
        ) : (
          <p className="m-0 px-3 py-6 text-center text-13 text-fg-muted">
            {t("iam.schema.noMatches")}
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
  onSelect: (resource_type: string) => void;
}): ReactElement {
  const t = useIamT();
  const graph = useMemo(
    () => buildSchemaGraph(resources, selectedResource?.resource_type ?? "", t),
    [resources, selectedResource?.resource_type, t],
  );

  if (resources.length === 0) {
    return (
      <section className="min-h-[34rem] rounded-md border border-border-subtle bg-sheet p-6 text-13 text-fg-muted">
        {t("iam.schema.noMatches")}
      </section>
    );
  }

  return (
    <section className="min-w-0 overflow-hidden rounded-md border border-border-subtle bg-sheet">
      <header className="flex min-w-0 items-start justify-between gap-3 border-b border-border-subtle px-4 py-3">
        <div className="min-w-0">
          <h2 className="m-0 truncate text-sm font-semibold text-fg">
            {t("iam.schema.permissionGraph")}
          </h2>
          {selectedResource ? (
            <Code className="mt-1" truncate tone="muted">
              {selectedResource.resource_type}
            </Code>
          ) : null}
        </div>
        <Badge tone="info">
          {t("iam.schema.nodeCount", { count: graph.nodes.length })}
        </Badge>
      </header>
      <GraphView
        nodes={graph.nodes}
        edges={graph.edges}
        nodeStyles={SCHEMA_NODE_STYLES}
        edgeStyles={SCHEMA_EDGE_STYLES}
        className="h-[34rem]"
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
      <section className="rounded-md border border-border-subtle bg-sheet p-6 text-13 text-fg-muted">
        {t("iam.schema.noneSelected")}
      </section>
    );
  }

  return (
    <aside className="min-w-0 rounded-md border border-border-subtle bg-sheet">
      <header className="border-b border-border-subtle px-4 py-3">
        <h2 className="m-0 truncate text-sm font-semibold text-fg">
          {resourceLabel(resource.resource_type)}
        </h2>
        <Code className="mt-1" truncate tone="muted">
          {resource.resource_type}
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
  const t = useIamT();
  return (
    <InspectorSection count={relations.length} title={t("iam.schema.relations")}>
      {relations.length > 0 ? (
        relations.map((relation) => (
          <InspectorRow
            key={relation.name}
            code={relation.name}
            title={titleLabel(relation.name)}
          >
            <ChipList
              values={relation.allowed_subject_types}
              empty={t("iam.schema.noSubjects")}
            />
          </InspectorRow>
        ))
      ) : (
        <EmptyInspectorRow>{t("iam.schema.noRelations")}</EmptyInspectorRow>
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
    <InspectorSection count={permissions.length} title={t("iam.schema.permissions")}>
      {permissions.length > 0 ? (
        permissions.map((permission) => (
          <InspectorRow
            key={permission.name}
            code={permission.name}
            title={titleLabel(permission.name)}
          >
            <ChipList
              values={permission.conditions.map((condition) => condition.name)}
              empty={t("iam.schema.noConditions")}
            />
          </InspectorRow>
        ))
      ) : (
        <EmptyInspectorRow>{t("iam.schema.noPermissions")}</EmptyInspectorRow>
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
        <Code truncate tone="muted">
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
        detail: t("iam.schema.resourceDetail", {
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
          detail:
            relation.allowed_subject_types.length === 1
              ? t("iam.schema.subjectCount.one", { count: relation.allowed_subject_types.length })
              : t("iam.schema.subjectCount.other", { count: relation.allowed_subject_types.length }),
        }),
      );
      edges.push({
        id: `contains:${resource.resource_type}:${relation.name}`,
        source: resource_id,
        target: relationId,
        kind: "contains",
        label: t("iam.schema.edge.contains"),
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
          detail:
            permission.conditions.length === 1
              ? t("iam.schema.conditionCount.one", { count: permission.conditions.length })
              : t("iam.schema.conditionCount.other", { count: permission.conditions.length }),
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

function resourceOptionId(listboxId: string, resource_type: string): string {
  return `${listboxId}-${resource_type.replace(/[^a-zA-Z0-9_-]+/g, "-")}`;
}
