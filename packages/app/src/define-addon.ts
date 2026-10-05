// Addon composition. Each addon describes itself once with `defineAddon`; an
// app folds the manifests into a single runtime with `composeAddons`. Every
// contribution is keyed, and the key decides what a second claim on it means:
//
// - Registry facts (routes, menu ids, widgets, status tones, icons, i18n keys,
//   forms, previews, data providers, layout providers) are unique: a second
//   addon claiming one is a composition-time error (app boot; `pnpm run test`
//   composes the full addon set, `typecheck`/`build` do not).
// - Menus and containers are layered: an addon declares in its own namespace
//   and alters what the addons it depends on declared; unrelated layers setting
//   one field collide (`menus.ts`, `containers.ts`).
//
// The ordered lists sort by sequence and contribution key, never by addon order.

import type { ResourceViewPreset } from "@angee/ui/views/resource-view-model";
export type { ResourceViewPreset } from "@angee/ui/views/resource-view-model";
import type { I18nResources } from "@angee/refine";
// The contribution contracts moved down into the binding (`@angee/ui` owns the
// runtime registry that consumes them); composition here builds manifests
// against them. Re-exported here so addon manifests import one composition seam.
import type {
  AppVocabulary,
  ComposedMenuItem,
  DrawerEdge,
  FormOverrideMap,
  MenuItem,
  PreviewContribution,
  RuntimeFormRegistration,
  RuntimeBrand,
  ComposedContainers,
  ContainersDeclaration,
  DockedDrawerContent,
  WidgetMap,
} from "@angee/ui/runtime";
import { RECORD_SEARCH_KEYS } from "@angee/ui/runtime";
import { STATUS_TONES, type StatusToneMap } from "@angee/ui/widgets/status-tones";
import { getIcon } from "@angee/ui/chrome/icon-registry";
import { optionToken } from "@angee/ui/widgets/types";
import { resolveShell, type PerspectiveDeclaration, type ResolvedShell, type ShellDeclaration } from "./shell";
import { compileMenus, type CompiledMenus, type MenuDeclarations } from "./menus";
import { layerAncestry } from "./layers";
import { compileContainers } from "./containers";
import { CORE_CONTAINERS } from "./core-containers";
import {
  parseDashboardSnapshot,
  type DashboardDefinition,
  type DashboardRegistry,
  type DashboardStore,
  type DashboardWidgetKind,
} from "@angee/ui/dashboard/headless";
import { BUILTIN_DASHBOARD_WIDGET_KINDS } from "@angee/ui/dashboard/kinds";
import {
  assertThemeDefinition,
  type ThemeDefinition,
} from "@angee/ui/theme";

export type {
  AppVocabulary,
  ComposedMenuItem,
  DockedDrawerContent,
  DrawerEdge,
  FormOverrideMap,
  MenuItem,
  PreviewContribution,
  RuntimeFormRegistration,
  RuntimeBrand,
  WidgetMap,
};

/** A route an addon contributes; the chrome is the refine layout named by `layout`. */
export interface AddonRoute {
  /** Stable, addon-namespaced name (e.g. `notes.detail`); unique across addons. */
  name: string;
  /** Router path pattern (e.g. `/notes`, `/notes/$id`). */
  path: string;
  /** Optional route name this route nests under in the rendered route tree. */
  parent?: string;
  /** Which refine layout renders this route's chrome (`console`, `public`, ...). */
  layout?: string;
  /**
   * Resource whose collection this route lists, e.g. `"integrate.OAuthClient"`.
   * Set it on
   * a routed collection action (not its `$id` child) to make the resource
   * followable: a relation field targeting it resolves this route as the detail
   * destination. Canonical claims are unique; explicit app roots may project it.
   */
  resource?: string;
  /** Model displayed by a projection of an existing resource. */
  recordModel?: string;
  /** Named shipped view selected by this collection route. */
  defaultResourceView?: string;
  /** This collection's record route owns records with the declared field value. */
  recordMatch?: { field: string; equals: string };
  /**
   * A capability (`current_user.capabilities`) the actor must hold. Without it this
   * route and its route descendants are unavailable: navigating there lands home,
   * as an unknown page does.
   */
  requires?: string;
}

/** A provider mounted once around one layout's chrome and routed content. */
export interface LayoutProviderContribution {
  id: string;
  layout: string;
  sequence?: number;
  /** The rendered binding supplies the native React component type. */
  component: unknown;
}

/** One addon's self-describing manifest. */
export interface AddonManifest {
  id: string;
  /**
   * Ids of the manifests this one depends on, transitively. The composed runtime
   * supplies them from `addon.toml`; shell facts layer along them.
   */
  dependsOn?: readonly string[];
  /** Home, brand and selected perspective, overriding the addons this one depends on. */
  shell?: ShellDeclaration;
  /** Named confinements a shell may select; ids are unique across addons. */
  perspectives?: Readonly<Record<string, PerspectiveDeclaration>>;
  /** @deprecated Declare `shell.brand`. */
  brand?: RuntimeBrand;
  routes?: readonly AddonRoute[];
  /**
   * Menu nodes keyed by id: own-namespace keys declare, other keys alter a node of
   * an addon this one depends on. The array form is the legacy declaration list.
   */
  menus?: readonly MenuItem[] | MenuDeclarations;
  widgets?: WidgetMap;
  /** Product status vocabulary; normalized keys cannot claim framework defaults or another addon's value. */
  statusTones?: StatusToneMap;
  i18n?: I18nResources;
  vocabulary?: readonly AppVocabulary[];
  resourceViews?: readonly ResourceViewPreset[];
  icons?: Readonly<Record<string, unknown>>;
  forms?: FormOverrideMap;
  /**
   * Children of named containers, keyed by address (`node#name`): own-namespace
   * keys declare children, other keys alter a dependency's; `only`, `except` and
   * `when` narrow what renders. An address on the addon's own node declares the container.
   */
  containers?: ContainersDeclaration;
  previews?: readonly PreviewContribution[];
  /** Addon-owned search keys that expire when the active record changes. */
  recordSearchKeys?: readonly string[];
  /**
   * Refine data providers an addon contributes, keyed by provider name. The SDK
   * manifest keeps the value opaque (only the name matters for collision
   * detection); the rendered binding owns the live `DataProvider` and overrides
   * the value type. `createApp` merges these into the schema-named providers it
   * passes to `<Refine dataProvider>`, so an addon can serve its own GraphQL
   * endpoint (e.g. the operator daemon) under its own provider name.
   */
  dataProviders?: Readonly<Record<string, unknown>>;
  layoutProviders?: readonly LayoutProviderContribution[];
  /** Code-owned dashboard defaults composed before persisted customizations. */
  dashboards?: readonly DashboardDefinition[];
  /** Namespaced widget kinds or explicit compatible replacements. */
  dashboardWidgetKinds?: readonly DashboardWidgetKind[];
  /** The persistence behind saved dashboards; at most one installed addon provides it. */
  dashboardStore?: DashboardStore;
  /** Installed visual implementations. Theme ids are globally unique. */
  themes?: readonly ThemeManifestContribution[];
}

export type ThemeManifestContribution =
  | ThemeDefinition<unknown>
  | { definition: ThemeDefinition<unknown> };

/** The merged runtime an app composes from its addon manifests. */
export interface ComposedAddons {
  /** Home, brand and perspective resolved across the addon layers. */
  shell: ResolvedShell;
  brand: RuntimeBrand | null;
  routes: readonly AddonRoute[];
  /** The logical menu tree: owns routes, trails and the active app. */
  menus: readonly ComposedMenuItem[];
  /** The compiled menu layers: navigation projection, removals, hidden nodes, provenance. */
  menuComposition: CompiledMenus;
  widgets: WidgetMap;
  statusTones: StatusToneMap;
  i18n: I18nResources;
  vocabulary: readonly AppVocabulary[];
  resourceViews: Readonly<Record<string, ResourceViewPreset>>;
  icons: Readonly<Record<string, unknown>>;
  forms: FormOverrideMap;
  /** The compiled containers: children per address, render-time narrowing, removals, provenance. */
  containers: ComposedContainers;
  previews: readonly PreviewContribution[];
  recordSearchKeys: readonly string[];
  dataProviders: Readonly<Record<string, unknown>>;
  layoutProviders: readonly LayoutProviderContribution[];
  dashboards: DashboardRegistry;
  themes: readonly ThemeManifestContribution[];
}

export interface ComposeAddonsOptions {
  /** Required composition-root model normalizer. */
  canonicalModelLabel: (spelling: string) => string;
}

/** Brand an object as an addon manifest, giving one greppable declaration site. */
export function defineAddon(manifest: Omit<AddonManifest, "menus"> & { menus?: readonly MenuItem[] }):
  Omit<AddonManifest, "menus"> & { menus?: readonly MenuItem[] };
export function defineAddon(manifest: Omit<AddonManifest, "menus"> & { menus: MenuDeclarations }):
  Omit<AddonManifest, "menus"> & { menus: MenuDeclarations };
export function defineAddon(manifest: AddonManifest): AddonManifest;
export function defineAddon(manifest: AddonManifest): AddonManifest {
  return manifest;
}

/**
 * Merge sequence-ordered contributions: dedupe by `keyOf` and sort by
 * `sequence`, then the contribution key. By default later groups win (an addon
 * overrides a default); pass `uniqueKind` to instead fail fast on a duplicate
 * key (two addons claiming one key is a collision, like widgets/previews).
 */
function mergeByKey<T extends { sequence?: number }>(
  groups: readonly (readonly T[])[],
  keyOf: (item: T) => string,
  uniqueKind?: string,
): T[] {
  const byKey = new Map<string, T>();
  for (const group of groups) {
    for (const item of group) {
      const key = keyOf(item);
      if (uniqueKind && byKey.has(key)) {
        throw new Error(
          `Two addons contribute ${uniqueKind} "${key}"; ${uniqueKind} keys must be unique.`,
        );
      }
      byKey.set(key, item);
    }
  }
  return [...byKey.values()].sort((a, b) => {
    const sequence = (a.sequence ?? 0) - (b.sequence ?? 0);
    if (sequence !== 0) return sequence;
    const left = keyOf(a);
    const right = keyOf(b);
    return left < right ? -1 : left > right ? 1 : 0;
  });
}

/** Assert a registry key is still unclaimed for one addon. */
function assertUnclaimed(
  registry: Record<string, unknown>,
  key: string,
  addonId: string,
  kind: string,
): void {
  if (Object.prototype.hasOwnProperty.call(registry, key)) {
    throw new Error(
      `Addon "${addonId}" redefines ${kind} "${key}" already contributed by another addon.`,
    );
  }
}

/**
 * Fold addon manifests into one runtime. Registry facts must be unique; ordered
 * contribution lists are merged by key and sorted by sequence.
 */
export function composeAddons(
  addons: readonly AddonManifest[],
  options: ComposeAddonsOptions,
): ComposedAddons {
  const canonicalizeModel = options.canonicalModelLabel;
  const ancestors = layerAncestry(addons);
  const shell = resolveShell(addons, ancestors);
  const routes: AddonRoute[] = [];
  const compiledMenus = compileMenus(addons, ancestors);
  const containers = compileContainers(addons, CORE_CONTAINERS, { ancestors, canonicalizeModel });
  const widgets: WidgetMap = {};
  const statusTones: Record<string, StatusToneMap[string]> = Object.create(null);
  const i18n: Record<string, Record<string, string>> = {};
  const icons: Record<string, unknown> = {};
  const forms: FormOverrideMap = {};
  const dataProviders: Record<string, unknown> = {};
  const previews: PreviewContribution[] = [];
  const routeNames: Record<string, true> = {};
  const resourceViews: Record<string, ResourceViewPreset> = {};
  const previewIds: Record<string, true> = {};
  const recordSearchKeys: Record<string, true> = {};
  const themes: ThemeManifestContribution[] = [];
  const themeIds: Record<string, true> = {};

  for (const addon of addons) {
    for (const contribution of addon.themes ?? []) {
      const definition = "definition" in contribution
        ? contribution.definition
        : contribution;
      assertThemeDefinition(definition);
      assertUnclaimed(themeIds, definition.id, addon.id, "theme id");
      themeIds[definition.id] = true;
      themes.push(contribution);
    }
    for (const key of addon.recordSearchKeys ?? []) {
      if (!key || RECORD_SEARCH_KEYS.includes(key)) {
        throw new Error(`Addon "${addon.id}" declares reserved or empty record search key "${key}".`);
      }
      assertUnclaimed(recordSearchKeys, key, addon.id, "record search key");
      recordSearchKeys[key] = true;
    }
    for (const preset of addon.resourceViews ?? []) {
      if (!preset.id.startsWith(`${addon.id}.`)) throw new Error(`Resource view "${preset.id}" must use addon namespace "${addon.id}".`);
      assertUnclaimed(resourceViews, preset.id, addon.id, "resource view");
      resourceViews[preset.id] = { ...preset, resource: canonicalizeModel(preset.resource), preset: preset.id };
    }
    if (addon.routes) {
      for (const route of addon.routes) {
        assertUnclaimed(routeNames, route.name, addon.id, "route name");
        routeNames[route.name] = true;
        routes.push(
          {
            ...route,
            ...(route.resource ? { resource: canonicalizeModel(route.resource) } : {}),
            ...(route.recordModel ? { recordModel: canonicalizeModel(route.recordModel) } : {}),
          },
        );
      }
    }
    if (addon.widgets) {
      for (const [key, widget] of Object.entries(addon.widgets)) {
        assertUnclaimed(widgets, key, addon.id, "widget");
        widgets[key] = widget;
      }
    }
    if (addon.icons) {
      for (const [name, icon] of Object.entries(addon.icons)) {
        assertUnclaimed(icons, name, addon.id, "icon");
        icons[name] = icon;
      }
    }
    for (const [value, tone] of Object.entries(addon.statusTones ?? {})) {
      const key = optionToken(value);
      if (!key) throw new Error(`Addon "${addon.id}" declares an empty status tone key.`);
      if (Object.values(STATUS_TONES).some((values) => values.includes(key))) {
        throw new Error(`Addon "${addon.id}" redefines framework status tone "${key}".`);
      }
      assertUnclaimed(statusTones, key, addon.id, "status tone");
      statusTones[key] = tone;
    }
    if (addon.forms) {
      for (const [model, form] of Object.entries(addon.forms)) {
        const canonicalModel = canonicalizeModel(model);
        assertUnclaimed(forms, canonicalModel, addon.id, "form override");
        if (isRuntimeFormRegistration(form)) {
          const registeredModel = canonicalizeModel(form.resource);
          if (registeredModel !== canonicalModel) {
            throw new Error(
              `Addon "${addon.id}" registers form key "${canonicalModel}" for component resource "${registeredModel}".`,
            );
          }
          forms[canonicalModel] = { ...form, resource: canonicalModel };
        } else {
          forms[canonicalModel] = form;
        }
      }
    }
    if (addon.dataProviders) {
      for (const [name, provider] of Object.entries(addon.dataProviders)) {
        assertUnclaimed(dataProviders, name, addon.id, "data provider");
        dataProviders[name] = provider;
      }
    }
    if (addon.i18n) {
      for (const [namespace, messages] of Object.entries(addon.i18n)) {
        if (namespace === "ui") {
          throw new Error(`Addon "${addon.id}" declares the reserved "ui" i18n namespace.`);
        }
        const target = (i18n[namespace] ??= {});
        for (const [key, value] of Object.entries(messages)) {
          assertUnclaimed(target, key, addon.id, `i18n key "${namespace}.${key}"`);
          target[key] = value;
        }
      }
    }
    if (addon.previews) {
      for (const preview of addon.previews) {
        assertUnclaimed(previewIds, preview.id, addon.id, "preview");
        previews.push(preview);
      }
    }
  }

  if (shell.brand && !getIcon(icons, shell.brand.mark)) {
    throw new Error(`Brand mark "${shell.brand.mark}" is not registered by any addon.`);
  }

  return {
    shell,
    brand: shell.brand,
    routes,
    menus: compiledMenus.logical,
    menuComposition: compiledMenus,
    widgets,
    statusTones,
    i18n,
    vocabulary: addons.flatMap((addon) => addon.vocabulary ?? []),
    resourceViews,
    icons,
    forms,
    dataProviders,
    layoutProviders: mergeByKey(
      addons.map((addon) => addon.layoutProviders ?? []),
      (provider) => `${provider.layout}\0${provider.id}`,
      "layout provider",
    ),
    containers,
    previews,
    recordSearchKeys: Object.keys(recordSearchKeys).sort(),
    dashboards: composeDashboardRegistry(addons, canonicalizeModel),
    themes: themes.sort((left, right) => {
      const leftId = "definition" in left ? left.definition.id : left.id;
      const rightId = "definition" in right ? right.definition.id : right.id;
      return leftId.localeCompare(rightId);
    }),
  };
}

function composeDashboardRegistry(
  addons: readonly AddonManifest[],
  canonicalizeModel: (spelling: string) => string,
): DashboardRegistry {
  const definitions: Record<string, DashboardDefinition> = {};
  const resourceDefaults: Record<string, string> = {};
  for (const addon of addons) {
    for (const definition of addon.dashboards ?? []) {
      if (!definition.key.startsWith(`${addon.id}.`)) {
        throw new Error(`Addon "${addon.id}" dashboard key "${definition.key}" must use the addon namespace.`);
      }
      assertUnclaimed(definitions, definition.key, addon.id, "dashboard");
      const resource = definition.resource ? canonicalizeModel(definition.resource) : undefined;
      const snapshot = parseDashboardSnapshot({
        schemaVersion: 1,
        columns: definition.columns ?? 12,
        widgets: definition.widgets,
      });
      definitions[definition.key] = { ...definition, ...snapshot, ...(resource ? { resource } : {}) };
      if (resource) {
        assertUnclaimed(resourceDefaults, resource, addon.id, "resource dashboard default");
        resourceDefaults[resource] = definition.key;
      }
    }
  }

  const byContribution = new Set<string>();
  const widgetKinds = Object.fromEntries(
    BUILTIN_DASHBOARD_WIDGET_KINDS.map((kind) => [kind.id, kind]),
  ) as Record<string, DashboardWidgetKind>;
  const replacements = new Map<string, DashboardWidgetKind>();
  for (const addon of addons) {
    for (const kind of addon.dashboardWidgetKinds ?? []) {
      if (byContribution.has(kind.contributionId)) {
        throw new Error(`Dashboard widget contribution "${kind.contributionId}" is duplicated.`);
      }
      byContribution.add(kind.contributionId);
      if (kind.replaces) {
        const existing = widgetKinds[kind.replaces];
        if (!existing) throw new Error(`Dashboard widget replacement targets unknown kind "${kind.replaces}".`);
        if (replacements.has(kind.replaces)) throw new Error(`Dashboard widget kind "${kind.replaces}" has competing replacements.`);
        if (kind.shape !== existing.shape || kind.version !== existing.version) {
          throw new Error(`Dashboard widget replacement "${kind.contributionId}" is incompatible with "${kind.replaces}".`);
        }
        replacements.set(kind.replaces, { ...kind, id: kind.replaces });
      } else {
        assertUnclaimed(widgetKinds, kind.id, addon.id, "dashboard widget kind");
        widgetKinds[kind.id] = kind;
      }
    }
  }
  for (const [id, replacement] of replacements) widgetKinds[id] = replacement;

  const providers = addons.filter((addon) => addon.dashboardStore);
  if (providers.length > 1) {
    throw new Error(`Addons ${providers.map((addon) => `"${addon.id}"`).join(", ")} each provide the dashboard store; install one.`);
  }
  return { definitions, resourceDefaults, widgetKinds, store: providers[0]?.dashboardStore ?? null };
}

function isRuntimeFormRegistration(value: unknown): value is RuntimeFormRegistration {
  return Boolean(
    value && typeof value === "object" && "resource" in value && "Component" in value,
  );
}

