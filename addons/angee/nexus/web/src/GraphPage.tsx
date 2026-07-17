import * as React from "react";
import { partyMergePath } from "@angee/parties";
import { useAuthoredQuery } from "@angee/refine";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBanner,
  GraphView,
  LoadingPanel,
  Page,
  PageBody,
  PageHeader,
  RailPanel,
  RelationPicker,
  SegmentedControl,
  Tag,
  recordPath,
  useChatter,
} from "@angee/ui";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";

import { NexusGraphParties, NexusPartyGraph } from "./documents";
import {
  edgeStyles,
  graphEdges,
  graphNodes,
  isPartyNode,
  nodeStyles,
  type NexusEdge,
  type NexusNode,
} from "./graph-data";
import { useNexusT } from "./i18n";

const ROOT_LIMIT = 200;
const GRAPH_LIMIT = 60;

type GraphLens = "ego" | "circle" | "identity";

/** Bounded, actor-scoped relationship explorer over the shared GraphView owner. */
export function GraphPage(): React.ReactElement {
  const t = useNexusT();
  const search = useSearch({ strict: false }) as Readonly<Record<string, unknown>>;
  const navigate = useNavigate();
  const { setActiveTab, setCollapsed } = useChatter();
  const roots = useAuthoredQuery(
    NexusGraphParties,
    { limit: ROOT_LIMIT },
    { models: ["parties.Party", "parties.Circle"] },
  );
  const options = React.useMemo(
    () => [
      ...(roots.data?.parties ?? []).map((party) => ({
        value: `party:${party.id}`,
        label: party.display_name,
      })),
      ...(roots.data?.circles ?? []).map((circle) => ({
        value: `circle:${circle.id}`,
        label: `${t("graph.circlePrefix")} · ${circle.name}`,
      })),
    ],
    [roots.data, t],
  );
  const searchedRoot = typeof search.root === "string" ? search.root : "";
  const selectedRoot = options.some((option) => option.value === searchedRoot)
    ? searchedRoot
    : (options[0]?.value ?? "");
  const [rootKind, rootId = ""] = selectedRoot.split(":", 2);
  const lens = isLens(search.lens) ? search.lens : "ego";
  const graph = useAuthoredQuery(
    NexusPartyGraph,
    {
      rootId: rootKind === "party" ? rootId : null,
      circleId: rootKind === "circle" ? rootId : null,
      lenses: [lens],
      depth: 1,
      limit: GRAPH_LIMIT,
    },
    {
      enabled: Boolean(rootId),
      models: [
        "nexus.Tie",
        "nexus.Cadence",
        "parties.Party",
        "parties.Relationship",
        "parties.CircleMember",
        "parties.PartyHandle",
        "parties.Handle",
      ],
    },
  );
  const nodes = React.useMemo(() => graphNodes(graph.data?.party_graph?.nodes), [graph.data]);
  const edges = React.useMemo(() => graphEdges(graph.data?.party_graph?.edges), [graph.data]);
  const [selectedNodeIds, setSelectedNodeIds] = React.useState<readonly string[]>([]);
  const [selectedEdgeId, setSelectedEdgeId] = React.useState<string | null>(null);

  React.useEffect(() => {
    setSelectedNodeIds([]);
    setSelectedEdgeId(null);
  }, [lens, selectedRoot]);

  const selectedNodes = selectedNodeIds
    .map((id) => nodes.find((node) => node.id === id))
    .filter((node): node is NexusNode => Boolean(node));
  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId) ?? null;
  const setSearch = React.useCallback(
    (patch: Record<string, unknown>) => {
      void navigate({ search: (current) => ({ ...current, ...patch }) });
    },
    [navigate],
  );

  if (roots.fetching && options.length === 0) return <LoadingPanel />;
  if (roots.error) return <EmptyState fill icon="triangle-alert" title={roots.error.message} />;
  if (options.length === 0) {
    return <EmptyState fill icon="users" title={t("graph.empty.title")} description={t("graph.empty.description")} />;
  }

  return (
    <Page>
      <PageHeader
        title={t("graph.title")}
        description={t("graph.description")}
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <div className="w-64">
              <RelationPicker
                aria-label={t("graph.root")}
                value={selectedRoot}
                options={options}
                searchPlaceholder={t("graph.searchRoots")}
                onChange={(root) => setSearch({ root })}
              />
            </div>
            <SegmentedControl<GraphLens>
              aria-label={t("graph.lens")}
              value={lens}
              options={[
                { value: "ego", label: t("graph.lens.ego") },
                { value: "circle", label: t("graph.lens.circle") },
                { value: "identity", label: t("graph.lens.identity") },
              ]}
              onValueChange={(next) => setSearch({ lens: next })}
            />
          </div>
        }
      />
      <PageBody gutter="none" scroll="hidden">
        {graph.fetching && !graph.data ? (
          <LoadingPanel />
        ) : graph.error ? (
          <div className="p-5"><ErrorBanner description={graph.error.message} /></div>
        ) : (
          <div className="grid h-full min-h-[34rem] grid-cols-[minmax(0,1fr)_minmax(18rem,22rem)] overflow-hidden bg-canvas">
            <div className="relative min-h-0 border-r border-border-subtle">
              {nodes.length === 0 ? (
                <EmptyState fill icon="radar" title={t("graph.noResults")} />
              ) : (
                <GraphView
                  className="h-full"
                  nodes={nodes}
                  edges={edges}
                  nodeStyles={nodeStyles}
                  edgeStyles={edgeStyles}
                  layout={{ rankdir: "LR" }}
                  onNodesSelect={(selected) => {
                    setSelectedNodeIds(selected.map((node) => node.id));
                    if (selected.length > 0) setSelectedEdgeId(null);
                  }}
                  onEdgeSelect={(edge) => {
                    setSelectedEdgeId(edge?.id ?? null);
                    if (edge) setSelectedNodeIds([]);
                  }}
                />
              )}
              {graph.data?.party_graph?.truncated ? (
                <Badge className="absolute left-3 top-3" tone="warning">{t("graph.truncated")}</Badge>
              ) : null}
            </div>
            <Inspector
              nodes={selectedNodes}
              edge={selectedEdge}
              openTimeline={(node) => {
                setActiveTab(isCircleNode(node) ? "feed" : "timeline");
                setCollapsed(false);
                const path = nodePath(node);
                if (path) void navigate({ to: path });
              }}
            />
          </div>
        )}
      </PageBody>
    </Page>
  );
}

function Inspector({
  nodes,
  edge,
  openTimeline,
}: {
  nodes: readonly NexusNode[];
  edge: NexusEdge | null;
  openTimeline: (node: NexusNode) => void;
}): React.ReactElement {
  const t = useNexusT();
  const selectedParties = nodes.filter((node) => isPartyNode(node));
  return (
    <aside className="min-h-0 overflow-auto bg-sheet-1 p-3">
      <RailPanel title={t("graph.inspector")} count={nodes.length || (edge ? 1 : undefined)} empty={t("graph.inspector.empty")}>
        {edge ? <EdgeDetails edge={edge} /> : null}
        {!edge && nodes.length > 0 ? (
          <div className="grid gap-3">
            {nodes.map((node) => {
              const path = nodePath(node);
              return (
                <div key={node.id} className="rounded-6 border border-border-subtle bg-sheet p-3">
                  <div className="font-medium text-fg">{node.title}</div>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    <Tag tone="neutral">{node.kind}</Tag>
                    {node.meta?.cadence?.cadence_days ? (
                      <Tag tone="warning">{t("graph.cadenceDays", { count: node.meta.cadence.cadence_days })}</Tag>
                    ) : null}
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {path ? <Button asChild size="sm"><Link to={path}>{t("graph.openRecord")}</Link></Button> : null}
                    {isPartyNode(node) || isCircleNode(node) ? (
                      <Button size="sm" variant="ghost" onClick={() => openTimeline(node)}>{t("graph.openTimeline")}</Button>
                    ) : null}
                  </div>
                </div>
              );
            })}
            {nodes.length === 2 && selectedParties.length === 2 ? (
              <Button asChild variant="primary">
                <Link to={partyMergePath(selectedParties[0].id, selectedParties[1].id)}>{t("graph.merge")}</Link>
              </Button>
            ) : (
              <p className="text-2xs text-fg-muted">{t("graph.mergeHint")}</p>
            )}
          </div>
        ) : null}
      </RailPanel>
    </aside>
  );
}

function EdgeDetails({ edge }: { edge: NexusEdge }): React.ReactElement {
  const t = useNexusT();
  const path = edgePath(edge);
  const relationshipLabel = edge.kind === "relationship" && edge.meta?.kind_name
    ? [edge.meta.kind_name, edge.meta.kind_inverse_name].filter(Boolean).join(" ↔ ")
    : null;
  return (
    <div className="grid gap-2 text-13">
      <div className="font-medium text-fg">{relationshipLabel ?? edge.label ?? edge.kind}</div>
      <div className="flex flex-wrap gap-1.5">
        <Tag tone={edge.kind === "tie_fading" ? "warning" : "neutral"}>{edge.kind.replaceAll("_", " ")}</Tag>
        {typeof edge.meta?.gravity === "number" ? <Tag tone="brand">{t("ties.gravity")} {edge.meta.gravity.toFixed(2)}</Tag> : null}
      </div>
      {path ? <Button asChild size="sm"><Link to={path}>{t("graph.openRecord")}</Link></Button> : null}
    </div>
  );
}

function nodePath(node: NexusNode): string | null {
  const routes: Record<string, string> = {
    "parties.Person": "/parties/people",
    "parties.Organization": "/parties/organizations",
    "parties.Circle": "/parties/circles",
    "parties.Handle": "/parties/handles",
  };
  const base = typeof node.meta?.model === "string" ? routes[node.meta.model] : undefined;
  return base ? recordPath(base, node.id) : null;
}

function edgePath(edge: NexusEdge): string | null {
  const routes: Record<string, string> = {
    "nexus.Tie": "/nexus/ties",
    "parties.Relationship": "/parties/relationships",
  };
  const model = typeof edge.meta?.model === "string" ? edge.meta.model : "";
  const recordId = typeof edge.meta?.record_id === "string" ? edge.meta.record_id : "";
  return routes[model] && recordId ? recordPath(routes[model], recordId) : null;
}

function isLens(value: unknown): value is GraphLens {
  return value === "ego" || value === "circle" || value === "identity";
}

function isCircleNode(node: NexusNode): boolean {
  return node.meta?.model === "parties.Circle";
}
