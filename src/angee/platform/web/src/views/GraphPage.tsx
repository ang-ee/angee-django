import { useCallback, useMemo, useState, type ReactElement } from "react";
import { useNavigate } from "@tanstack/react-router";

import {
  ErrorBanner, GraphView, InlineEmpty, LoadingPanel, NavLink, Pager, PaneToolbar,
  PaneToolbarHost, PrimaryPanePublisher, RecordHeader, SearchInput, TreeView,
  routeSearchParam, updateRouteSearch, useRouteHref, useRouteSearch,
  type GraphViewNode, type GraphViewNodeStyle,
} from "@angee/ui";

import { usePlatformT } from "../i18n";
import { PLATFORM_GRAPH_PAGE_SIZE, usePlatformModelGraph } from "../lib/explorer";

const NODE_STYLES: Record<"model", GraphViewNodeStyle> = {
  model: {
    width: 208,
    height: 64,
    borderColor: "var(--border-strong)",
    badgeTone: "neutral",
  },
};
const GRAPH_LAYOUT = { rankdir: "LR" } as const;

export function GraphPage(): ReactElement {
  const t = usePlatformT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const routeSearch = useRouteSearch();
  const [search, setSearch] = useState("");
  const graph = usePlatformModelGraph({
    model: routeSearchParam(routeSearch, "model"),
    relationPage: Number(routeSearchParam(routeSearch, "relationPage") ?? 1),
  });
  const selectModel = useCallback((model: string) => {
    void navigate({ to: ".", search: updateRouteSearch({ model, relationPage: undefined }) });
  }, [navigate]);
  const selectNode = useCallback((node: GraphViewNode<"model">) => selectModel(node.id), [selectModel]);
  const visibleModels = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return graph.models.filter((model) =>
      `${model.label} ${model.verbose_name} ${model.addon_label}`.toLowerCase().includes(needle));
  }, [graph.models, search]);
  const ready = !graph.error && !(graph.isFetching && graph.models.length === 0);
  const navigator = useMemo(() => ready ? (
    <PaneToolbarHost role="navigation" aria-label={t("col.models")}>
      <PaneToolbar search={<SearchInput value={search} placeholder={t("graph.search")}
        onChange={(event) => setSearch(event.currentTarget.value)} onClear={() => setSearch("")} />} />
      <TreeView rows={visibleModels} rowKey="label" label="label"
        selectedId={graph.model?.label} onSelect={(model) => selectModel(model.label)}
        emptyContent={t("empty.models")} className="min-h-0 flex-1 overflow-auto p-2" />
    </PaneToolbarHost>
  ) : null, [ready, search, visibleModels, graph.model, selectModel, t]);

  return (
    <>
      <PrimaryPanePublisher node={navigator} />
      {graph.error ? <ErrorBanner className="m-3" description={graph.error.message} />
        : !ready ? <LoadingPanel density="inline" message={t("detail.model.loading")} />
          : !graph.model ? <InlineEmpty label={t("empty.models")} /> : (
            <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
              <RecordHeader density="compact" crumbs={t("col.graph")} title={graph.model.verbose_name}
                meta={<NavLink href={routeHref("platform.models.record", { id: graph.model.label })}
                  variant="inline">{graph.model.label}</NavLink>}
                actions={<Pager page={graph.relationPage} pageSize={PLATFORM_GRAPH_PAGE_SIZE}
                  total={graph.relationTotal} unit={t("graph.relationships")} labelElement="span"
                  onPageChange={(relationPage) => {
                    void navigate({ to: ".", search: updateRouteSearch({ model: graph.model?.label, relationPage: String(relationPage) }) });
                  }} />} />
              <GraphView key={`${graph.model.label}:${graph.relationPage}`} nodes={graph.nodes} edges={graph.edges} nodeStyles={NODE_STYLES}
                layout={GRAPH_LAYOUT} initialView={{ anchorNodeId: graph.model.label }}
                className="min-h-0 flex-1 bg-sheet" onNodeClick={selectNode} />
            </div>
          )}
    </>
  );
}
