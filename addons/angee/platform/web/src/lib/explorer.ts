import type { UseQueryResult } from "@tanstack/react-query";
import { useMemo } from "react";

import {
  useAuthoredQuery,
  type DocumentData,
} from "@angee/refine";
import type { GraphViewEdge, GraphViewNode } from "@angee/ui";

import {
  PlatformExplorer,
  type PlatformAddonData,
  type PlatformExplorerData,
  type PlatformModelData,
} from "../documents";
import {
  modelGraphEdges,
  modelGraphNodes,
} from "./rows";

export type PlatformExplorerResult = DocumentData<typeof PlatformExplorer>;

const EMPTY_EXPLORER: PlatformExplorerData = {
  pending_addon_changes: null,
  addons: [],
  models: [],
  edges: [],
};

export interface PlatformModelGraphScope {
  model?: string | null;
  relationPage?: number;
}

export type PlatformExplorerQuery = UseQueryResult<PlatformExplorerResult> & {
  explorer: PlatformExplorerData | null;
}

export interface PlatformAddonDetail {
  addon: PlatformAddonData | undefined;
  dependsOn: readonly string[];
  dependedBy: readonly string[];
  modelLabels: readonly string[];
}

export interface PlatformModelDetail {
  model: PlatformModelData | undefined;
  dependedBy: readonly string[];
}

export type PlatformAddonDetailResult = PlatformExplorerQuery & PlatformAddonDetail & {
  notFound: boolean;
}

export type PlatformModelDetailResult = PlatformExplorerQuery & PlatformModelDetail & {
  notFound: boolean;
}

export const PLATFORM_GRAPH_PAGE_SIZE = 50;

interface PlatformModelGraphSelection {
  models: readonly PlatformModelData[];
  model: PlatformModelData | undefined;
  relationPage: number;
  relationTotal: number;
  nodes: readonly GraphViewNode<"model">[];
  edges: readonly GraphViewEdge[];
}

export type PlatformModelGraphResult = UseQueryResult<PlatformExplorerResult> & PlatformModelGraphSelection;

function explorerOrEmpty(
  data: PlatformExplorerResult | undefined,
): PlatformExplorerData {
  return data?.platform_explorer ?? EMPTY_EXPLORER;
}

function sortedUnique(values: readonly string[]): string[] {
  return [...new Set(values)].sort();
}

export function selectPlatformAddonDetail(
  data: PlatformExplorerResult | undefined,
  id?: string | null,
): PlatformAddonDetail {
  const addons = explorerOrEmpty(data).addons;
  const addon = id ? addons.find((candidate) => candidate.id === id) : undefined;
  const ids = new Set(addons.map((candidate) => candidate.id));
  return {
    addon,
    dependsOn: sortedUnique((addon?.depends_on ?? []).filter((dep) => ids.has(dep))),
    dependedBy: addon?.depended_by ?? [],
    modelLabels: sortedUnique(addon?.model_labels ?? []),
  };
}

export function selectPlatformModelDetail(
  data: PlatformExplorerResult | undefined,
  id?: string | null,
): PlatformModelDetail {
  const models = explorerOrEmpty(data).models;
  return {
    model: id ? models.find((candidate) => candidate.label === id) : undefined,
    dependedBy: id
      ? models
          .filter((candidate) => candidate.depends_on.includes(id))
          .map((candidate) => candidate.label)
          .sort()
      : [],
  };
}

/** Page one selected model’s incident relationships without including peer-to-peer edges. */
export function selectPlatformModelGraph(
  data: PlatformExplorerResult | undefined,
  scope: PlatformModelGraphScope = {},
): PlatformModelGraphSelection {
  const explorer = explorerOrEmpty(data);
  const models = [...explorer.models].sort((a, b) => a.label.localeCompare(b.label));
  const model = models.find((candidate) => candidate.label === scope.model) ?? models[0];
  const incident = model ? explorer.edges.filter(
    (edge) => edge.source === model.label || edge.target === model.label,
  ).sort((a, b) => a.id.localeCompare(b.id)) : [];
  const requestedPage = scope.relationPage ?? 1;
  const relationPage = Math.max(1, Math.min(
    Number.isSafeInteger(requestedPage) ? requestedPage : 1,
    Math.max(1, Math.ceil(incident.length / PLATFORM_GRAPH_PAGE_SIZE)),
  ));
  const edges = incident.slice((relationPage - 1) * PLATFORM_GRAPH_PAGE_SIZE, relationPage * PLATFORM_GRAPH_PAGE_SIZE);
  const endpoints = new Set(edges.flatMap((edge) => [edge.source, edge.target]));
  if (model) endpoints.add(model.label);
  return {
    models, model, relationPage, relationTotal: incident.length,
    nodes: modelGraphNodes(models.filter((candidate) => endpoints.has(candidate.label)), model?.label),
    edges: modelGraphEdges(edges),
  };
}

export function usePlatformExplorer(): PlatformExplorerQuery {
  const query = useAuthoredQuery(PlatformExplorer);
  return {
    ...query,
    explorer: query.data?.platform_explorer ?? null,
  };
}

export function usePlatformAddon(
  id?: string | null,
): PlatformAddonDetailResult {
  const query = usePlatformExplorer();
  const detail = useMemo(
    () => selectPlatformAddonDetail(query.data, id),
    [query.data, id],
  );
  return { ...query, ...detail, notFound: !query.isFetching && !detail.addon };
}

export function usePlatformModel(
  id?: string | null,
): PlatformModelDetailResult {
  const query = usePlatformExplorer();
  const detail = useMemo(
    () => selectPlatformModelDetail(query.data, id),
    [query.data, id],
  );
  return { ...query, ...detail, notFound: !query.isFetching && !detail.model };
}

export function usePlatformModelGraph({
  model,
  relationPage,
}: PlatformModelGraphScope = {}): PlatformModelGraphResult {
  const query = useAuthoredQuery(PlatformExplorer);
  const graph = useMemo(
    () => selectPlatformModelGraph(query.data, { model, relationPage }),
    [query.data, model, relationPage],
  );
  return { ...query, ...graph };
}
