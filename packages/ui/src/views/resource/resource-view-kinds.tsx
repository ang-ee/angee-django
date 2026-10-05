import * as React from "react";

import type { CoreContainer, ResourceViewKindContent } from "../../runtime";
import {
  RESOURCE_VIEW_KIND_CAPABILITIES,
  availableResourceViewKinds,
  isBuiltInResourceViewKind,
  type BuiltInResourceViewKind,
  type ResourceViewKind,
} from "./resource-view-model";

const BUILT_IN_KINDS: Readonly<Record<BuiltInResourceViewKind, { labelKey: string; icon: string; sequence: number }>> = {
  list: { labelKey: "resourceToolbar.listView", icon: "list", sequence: 10 },
  board: { labelKey: "resourceToolbar.boardView", icon: "grid-2x2", sequence: 20 },
  calendar: { labelKey: "resourceToolbar.calendarView", icon: "calendar", sequence: 30 },
  gantt: { labelKey: "resourceToolbar.ganttView", icon: "chart-gantt", sequence: 40 },
  dashboard: { labelKey: "resourceToolbar.dashboardView", icon: "chart-no-axes-combined", sequence: 50 },
};

/**
 * A resource collection's containers. `#views` holds the view kinds its switcher
 * offers: the framework declares the built-in kinds (under the ids URLs and
 * saved views already carry), and addons contribute `<model>#views` children
 * such as `nexus.graph`; layers narrow either with `only` and `hide` (G-18).
 * `#utilities` holds collection utilities beside the toolbar.
 * `#search` holds shortcut declarations and page extras; the box is never a child.
 */
export const RESOURCE_CONTAINERS: readonly CoreContainer[] = [
  {
    address: "resource#views",
    models: true,
    children: Object.fromEntries(Object.entries(BUILT_IN_KINDS).map(([kind, { labelKey, icon, sequence }]) => [kind, {
      sequence,
      content: { labelKey, icon, capabilities: RESOURCE_VIEW_KIND_CAPABILITIES[kind as BuiltInResourceViewKind] },
    }])),
  },
  { address: "resource#utilities", models: true },
  { address: "resource#search", models: true, extras: true },
];

const ResourceViewKindsContext = React.createContext<ReadonlyMap<string, ResourceViewKindContent>>(
  new Map(Object.entries(BUILT_IN_KINDS).map(([kind, { labelKey, icon }]) =>
    [kind, { labelKey, icon, capabilities: RESOURCE_VIEW_KIND_CAPABILITIES[kind as BuiltInResourceViewKind] }])),
);

/** The offered kinds' switcher label, glyph, capabilities and (for contributed kinds) body. */
export const ResourceViewKindsProvider = ResourceViewKindsContext.Provider;

/** Every kind the enclosing collection offers, by id. */
export function useResourceViewKinds(): ReadonlyMap<string, ResourceViewKindContent> {
  return React.useContext(ResourceViewKindsContext);
}

/** One kind's declaration, as the enclosing collection offers it. */
export function useResourceViewKindContent(kind: ResourceViewKind | undefined): ResourceViewKindContent | undefined {
  const kinds = React.useContext(ResourceViewKindsContext);
  return kind === undefined ? undefined : kinds.get(kind);
}

/**
 * The kinds a collection offers, in container order: the `resource#views`
 * children its enclosing `ResourceViewKindsProvider` resolved, keeping a
 * built-in kind only where the page declares the data it needs.
 */
export function useOfferedResourceViewKinds(
  declared: Parameters<typeof availableResourceViewKinds>[0],
): readonly ResourceViewKind[] {
  const kinds = React.useContext(ResourceViewKindsContext);
  const runnable = React.useMemo(() => new Set<string>(availableResourceViewKinds(declared)), [declared]);
  return React.useMemo(
    () => [...kinds.keys()].filter((kind) => !isBuiltInResourceViewKind(kind) || runnable.has(kind)) as ResourceViewKind[],
    [kinds, runnable],
  );
}
