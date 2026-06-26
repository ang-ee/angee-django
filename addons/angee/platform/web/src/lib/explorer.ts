import { useMemo } from "react";

import {
  useAuthoredQuery,
  type AuthoredQueryResult,
  type GraphViewEdge,
  type GraphViewNode,
} from "@angee/ui";
import type { DocumentData } from "@angee/refine";

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
  addons: [],
  models: [],
  edges: [],
};

export interface PlatformModelGraphScope {
  model?: string | null;
}

export interface PlatformExplorerQuery
  extends AuthoredQueryResult<PlatformExplorerResult> {
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

export interface PlatformAddonDetailResult
  extends PlatformExplorerQuery,
    PlatformAddonDetail {
  notFound: boolean;
}

export interface PlatformModelDetailResult
  extends PlatformExplorerQuery,
    PlatformModelDetail {
  notFound: boolean;
}

export interface PlatformModelGraphResult
  extends AuthoredQueryResult<PlatformExplorerResult> {
  nodes: readonly GraphViewNode<"model">[];
  edges: readonly GraphViewEdge[];
}

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
    dependedBy: id
      ? addons
          .filter((candidate) => candidate.depends_on.includes(id))
          .map((candidate) => candidate.id)
          .sort()
      : [],
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

export function selectPlatformModelGraph(
  data: PlatformExplorerResult | undefined,
  scope: PlatformModelGraphScope = {},
): Pick<PlatformModelGraphResult, "nodes" | "edges"> {
  const explorer = explorerOrEmpty(data);
  return {
    nodes: modelGraphNodes(explorer.models, scope.model),
    edges: modelGraphEdges(explorer.edges),
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
  return { ...query, ...detail, notFound: !query.fetching && !detail.addon };
}

export function usePlatformModel(
  id?: string | null,
): PlatformModelDetailResult {
  const query = usePlatformExplorer();
  const detail = useMemo(
    () => selectPlatformModelDetail(query.data, id),
    [query.data, id],
  );
  return { ...query, ...detail, notFound: !query.fetching && !detail.model };
}

export function usePlatformModelGraph({
  model,
}: PlatformModelGraphScope = {}): PlatformModelGraphResult {
  const query = useAuthoredQuery(PlatformExplorer);
  const graph = useMemo(
    () => selectPlatformModelGraph(query.data, { model }),
    [query.data, model],
  );
  return { ...query, ...graph };
}
