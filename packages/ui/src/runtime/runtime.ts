import { useCallback, useEffect, useMemo, useState } from "react";
import { useMatches, useRouterState } from "@tanstack/react-router";
import type {
  MessageResources,
  MessageVars,
} from "@angee/refine";
import {
  canonicalModelLabelOrNull,
  useSchemaFieldMetadata,
} from "@angee/metadata";

import type {
  RuntimeVocabulary,
  ChatterRoute,
  FormOverrideMap,
  PreviewContribution,
  RuntimeBrand,
  RuntimeComposition,
  WidgetMap,
} from "./contracts";
import type { ComposedContainers, ContainerScope } from "./containers";
import { makeContext } from "./make-context";
import { createAngeeI18nInstance } from "./i18n";
import {
  createRouteHref,
  type RouteHref,
  type RuntimeResourceRoutes,
} from "./route-href";
import type { ResourceViewPreset } from "../views/resource/model/favorites";
import type { DashboardRegistry } from "../dashboard/headless";
import type { ThemeContribution } from "../theme";
import type { StatusToneMap } from "../widgets/status-tones";
import { setHumanDateLocale } from "../widgets/date-format";

export const DEFAULT_LOGIN_PATH = "/login";
export const HOME_PATH_PREFERENCE_KEY = "homePath";
export const ROUTE_SHORTCUTS_PREFERENCE_KEY = "chrome.routeShortcuts";

const FALLBACK_I18N = createAngeeI18nInstance({});
const PLURAL_SUFFIXES = ["zero", "one", "two", "few", "many", "other"] as const;

export interface RuntimeRouteShortcut {
  id: string;
  label: string;
  path: string;
  icon?: string;
}

/** Read generic dynamic route shortcuts without importing an addon's domain types. */
export function readRuntimeRouteShortcuts(
  preferences: RuntimeUserPreferences | null | undefined,
): readonly RuntimeRouteShortcut[] {
  const raw = preferences?.[ROUTE_SHORTCUTS_PREFERENCE_KEY];
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const value = item as Record<string, unknown>;
    if (
      typeof value.id !== "string" || !value.id
      || typeof value.label !== "string" || !value.label
      || typeof value.path !== "string" || !value.path.startsWith("/")
      || seen.has(value.id)
    ) return [];
    seen.add(value.id);
    return [{
      id: value.id,
      label: value.label,
      path: value.path,
      ...(typeof value.icon === "string" && value.icon ? { icon: value.icon } : {}),
    }];
  });
}

export type { RuntimeResourceRoutes } from "./route-href";

export type ResourceRecordHrefLookup = (
  resource: string,
  id: string,
  row?: Readonly<Record<string, unknown>>,
) => string | undefined;

/**
 * The merged app runtime an app composes once from its addon manifests. The
 * registry lookups (`useWidget` / `useContainer` / `useT`) read from it;
 * there is no separate provider per registry.
 */
export interface AppRuntime {
  brand: RuntimeBrand | null;
  /** Host-selected menu root; this constrains navigation, never server access. */
  confineTo: string | null;
  widgets: WidgetMap;
  statusTones: StatusToneMap;
  i18n: RuntimeI18n | null;
  vocabulary: RuntimeVocabulary;
  resourceViews: Readonly<Record<string, ResourceViewPreset>>;
  defaultResourceView?: string;
  /** Presets targeted by menu entries for the active collection route. */
  menuResourceViewIds?: readonly string[];
  auth: RuntimeAuthState;
  logoutAction: RuntimeLogoutAction;
  userPreferences: RuntimeUserPreferencesState;
  icons: Readonly<Record<string, unknown>>;
  forms: FormOverrideMap;
  /** Inherited route policies for the shell aside. */
  chatterRoutes: readonly ChatterRoute[];
  /** Addon-owned detail search keys cleared by routed record navigation. */
  recordSearchKeys: readonly string[];
  previews: readonly PreviewContribution[];
  /** Composed dashboard definitions, kinds and optional persistence adapter. */
  dashboards: DashboardRegistry;
  /** Composed collection/record route names per resource id. */
  routesByResource: Readonly<Record<string, RuntimeResourceRoutes>>;
  /** Resolve a composed route name to an encoded href. */
  routeHref: RouteHref;
  /** App-owned sign-in destination shared by auth gates and chrome. */
  loginPath: string;
  /** Installed theme catalogue composed from addon contributions. */
  themes: readonly ThemeContribution[];
  /** The composed containers every container owner renders from. */
  containers?: ComposedContainers;
  /** The page's apps, routes and perspective, which container conditions read. */
  containerScope?: ContainerScope;
  /** How the composition came out; developer mode shows it. */
  composition?: RuntimeComposition | null;
  /** The active route's name and its app (menu root), per page. */
  activeRouteName?: string | null;
  /** Winning menu destination after URL specificity and the inherited route anchor. */
  activeMenuId?: string | null;
  activeApp?: string | null;
}

export interface RuntimeI18n {
  language?: string;
  on?: (event: "languageChanged", listener: (language: string) => void) => unknown;
  off?: (event: "languageChanged", listener: (language: string) => void) => unknown;
  getFixedT: (
    lng: string | readonly string[] | null,
    ns: string,
  ) => (key: string, options?: RuntimeTOptions) => unknown;
}

export type RuntimeUserPreferences = Record<string, unknown>;
export type RuntimeUserPreferencesPatch = (
  preferences: RuntimeUserPreferences,
) => RuntimeUserPreferences;

export interface RuntimeAuthUser {
  id: string;
  name: string;
  username?: string;
  email?: string;
  roles?: readonly string[];
}

export interface RuntimeAuthState {
  user: RuntimeAuthUser | null;
  status: "resolving" | "anonymous" | "authenticated";
  hasRole: (role: string) => boolean;
  /** Optional preview controller supplied by the app's identity owner. */
  viewAs?: RuntimeViewAs;
}

/** Server-authorized preview identities; the app owns transitions and transport. */
export interface RuntimeViewAs {
  viewAs: { userId: string } | null;
  currentUser: RuntimeAuthUser | null;
  realUser: RuntimeAuthUser | null;
  viewablePeople: readonly RuntimeAuthUser[];
  enter: (userId: string) => void;
  exit: () => void;
  pending?: boolean;
  error?: string | null;
}

const NO_VIEW_AS: RuntimeViewAs = {
  viewAs: null,
  currentUser: null,
  realUser: null,
  viewablePeople: [],
  enter: () => undefined,
  exit: () => undefined,
};

export interface RuntimeLogoutAction {
  logout: () => Promise<boolean>;
  fetching: boolean;
  error: Error | null;
}

export interface RuntimeUserPreferencesState {
  available: boolean;
  preferences: RuntimeUserPreferences;
  patchPreferences: (patch: RuntimeUserPreferencesPatch) => Promise<void>;
}

type RuntimeTOptions = MessageVars & {
  defaultValue?: string;
};

const ANONYMOUS_RUNTIME_AUTH: RuntimeAuthState = {
  user: null,
  status: "anonymous",
  hasRole: () => false,
};

const EMPTY_USER_PREFERENCES: RuntimeUserPreferences = {};

const EMPTY_RUNTIME: AppRuntime = {
  brand: null,
  confineTo: null,
  widgets: {},
  statusTones: {},
  i18n: null,
  vocabulary: { resources: {}, menus: {} },
  resourceViews: {},
  auth: ANONYMOUS_RUNTIME_AUTH,
  logoutAction: {
    logout: async () => false,
    fetching: false,
    error: null,
  },
  userPreferences: {
    available: false,
    preferences: EMPTY_USER_PREFERENCES,
    patchPreferences: async () => undefined,
  },
  icons: {},
  forms: {},
  chatterRoutes: [],
  recordSearchKeys: [],
  previews: [],
  dashboards: {
    definitions: {},
    resourceDefaults: {},
    widgetKinds: {},
    store: null,
  },
  routesByResource: {},
  routeHref: createRouteHref([]),
  loginPath: DEFAULT_LOGIN_PATH,
  themes: [],
};

const RuntimeContext = makeContext<AppRuntime>("AppRuntime");

/** Provide the runtime, filling any unset registry with its empty default. */
export function AppRuntimeProvider(props: {
  runtime: Partial<AppRuntime>;
  children: React.ReactNode;
}): React.ReactNode {
  const { runtime } = props;
  const parent = RuntimeContext.useMaybe();
  const [languageRevision, setLanguageRevision] = useState(0);
  const value = useMemo<AppRuntime>(
    () => ({ ...EMPTY_RUNTIME, ...(parent ?? {}), ...runtime }),
    [parent, runtime, languageRevision],
  );
  // Set before descendants render: the formatter API is pure and has no hook.
  if (value.i18n?.language) setHumanDateLocale(value.i18n.language);
  useEffect(() => {
    const i18n = value.i18n;
    if (!i18n?.on || !i18n.off) return;
    const onLanguageChanged = (language: string) => {
      setHumanDateLocale(language);
      setLanguageRevision((revision) => revision + 1);
    };
    i18n.on("languageChanged", onLanguageChanged);
    return () => { i18n.off?.("languageChanged", onLanguageChanged); };
  }, [value.i18n]);
  return RuntimeContext.Provider({ value, children: props.children });
}

/** The merged runtime, or the empty runtime when unprovided. */
export function useAppRuntime(): AppRuntime {
  return RuntimeContext.useMaybe() ?? EMPTY_RUNTIME;
}

/** The product identity contributed by the composed app, if any. */
export function useRuntimeBrand(): RuntimeBrand | null {
  return useAppRuntime().brand ?? null;
}

/** Preview state injected by the app; absent injection leaves preview inactive. */
export function useRuntimeViewAs(): RuntimeViewAs {
  return useRuntimeAuth().viewAs ?? NO_VIEW_AS;
}

/** The dashboard registry composed once by the app owner. */
export function useDashboardRegistry(): DashboardRegistry {
  return useAppRuntime().dashboards ?? EMPTY_RUNTIME.dashboards;
}

/** Look up a contributed widget by id. */
export function useWidget(id: string): unknown {
  return useAppRuntime().widgets[id];
}

/** Look up an addon-contributed legacy create override or complete form. */
export function useFormOverride(resource: string): FormOverrideMap[string] | undefined {
  // `?.` guards a `Partial<AppRuntime>` provider that spread `forms: undefined`.
  return useAppRuntime().forms?.[resource];
}

/**
 * The collection route base path for a resource (e.g. `"integrate.OAuthClient"` →
 * `"/integrate/providers"`), or `undefined` when no route lists it. Drives the
 * relation-follow affordance; a resource without a routed list offers no link.
 */
export function useResourceRoute(resource: string): string | undefined {
  const route = useResourceRoutes(resource);
  const routeHref = useAppRuntime().routeHref;
  return route ? routeHref.maybe(route.collection) : undefined;
}

function useResourceRoutes(resource: string): RuntimeResourceRoutes | undefined {
  const metadata = useSchemaFieldMetadata();
  const canonicalResource = useMemo(() => {
    if (!resource) return "";
    return canonicalModelLabelOrNull(
      metadata.resources ?? [],
      resource,
      "resource route lookup",
    ) ?? "";
  }, [metadata, resource]);
  // `?.` guards a `Partial<AppRuntime>` provider that spread `routesByResource: undefined`.
  return useAppRuntime().routesByResource?.[canonicalResource];
}

/** Build record hrefs from a resource's composed collection route, when routed. */
export function useResourceRecordHref(
  resource: string,
): ((id: string, row?: Readonly<Record<string, unknown>>) => string | undefined) | undefined {
  const routes = useResourceRoutes(resource);
  const routeHref = useAppRuntime().routeHref;
  return useMemo(
    () =>
      routes?.record === undefined
        ? undefined
        : (id: string, row?: Readonly<Record<string, unknown>>) => {
          const record = recordDestination(routes, row);
          return record ? routeHref.maybe(record.name, {
            [record.param]: id,
          }) : undefined;
        },
    [routes, routeHref],
  );
}

/** Row projections needed to choose an app-owned record destination. */
export function useResourceRecordMatchFields(resource: string): readonly string[] {
  const routes = useResourceRoutes(resource);
  return useMemo(() => [...new Set(routes?.recordDestinations?.map(({ match }) => match.field) ?? [])], [routes]);
}

/** Resolve any resource-backed record href, degrading when its addon is absent. */
export function useResourceRecordHrefLookup(): ResourceRecordHrefLookup {
  const metadata = useSchemaFieldMetadata();
  const { routesByResource, routeHref } = useAppRuntime();
  return useCallback(
    (resource: string, id: string, row?: Readonly<Record<string, unknown>>) => {
      if (!resource || !id) return undefined;
      const canonicalResource = canonicalModelLabelOrNull(
        metadata.resources ?? [],
        resource,
        "resource record route lookup",
      );
      const record = canonicalResource
        ? recordDestination(routesByResource?.[canonicalResource], row)
        : undefined;
      return record
        ? routeHref.maybe(record.name, { [record.param]: id })
        : undefined;
    },
    [metadata, routeHref, routesByResource],
  );
}

function recordDestination(
  routes: RuntimeResourceRoutes | undefined,
  row?: Readonly<Record<string, unknown>>,
): RuntimeResourceRoutes["record"] {
  if (!routes) return undefined;
  if (!routes.recordDestinations?.length) return routes.record;
  const matching = routes.recordDestinations.filter(({ match }) => {
    const value = match.field.split(".").reduce<unknown>((current, key) =>
      current && typeof current === "object" ? (current as Record<string, unknown>)[key] : undefined, row);
    return value === match.equals;
  });
  if (matching.length > 1) throw new Error("Record matches more than one app route.");
  return matching[0]?.record ?? routes.recordFallback;
}

/** The app-composed, fail-fast route href builder. */
export function useRouteHref(): RouteHref {
  return RuntimeContext.use().routeHref;
}

/** Resolve the active declaration by the router's matched path. */
export function useActiveRoute<T extends { path: string }>(routes: readonly T[]): T | undefined {
  const matches = useMatches();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const fullPath = matches.at(-1)?.fullPath ?? pathname;
  return useMemo(
    () => routes.find((route) => route.path.replace(/\/$/, "") === fullPath?.replace(/\/$/, "")),
    [routes, fullPath],
  );
}

/** The app-owned login destination used by shell and IAM surfaces. */
export function useLoginPath(): string {
  return useAppRuntime().loginPath ?? DEFAULT_LOGIN_PATH;
}

/** The shell-readable auth state supplied by the app-owned auth provider. */
export function useRuntimeAuth(): RuntimeAuthState {
  return useAppRuntime().auth ?? ANONYMOUS_RUNTIME_AUTH;
}

/** The shell-readable logout action supplied by the app-owned auth provider. */
export function useRuntimeLogoutAction(): RuntimeLogoutAction {
  return useAppRuntime().logoutAction ?? EMPTY_RUNTIME.logoutAction;
}

/** User preferences persisted by the app-owned auth provider. */
export function useRuntimeUserPreferences(): RuntimeUserPreferencesState {
  return useAppRuntime().userPreferences ?? EMPTY_RUNTIME.userPreferences;
}

/** The addon-contributed file-preview renderers, in composed order. */
export function usePreviews(): readonly PreviewContribution[] {
  return useAppRuntime().previews;
}

/** The route metadata Chatter uses to build the active view envelope. */
export function useChatterRoutes(): readonly ChatterRoute[] {
  return useAppRuntime().chatterRoutes ?? [];
}

/** A translator bound to one namespace; resolves keys against merged i18n. */
export function useT(namespace: string): (key: string, vars?: MessageVars) => string {
  const { i18n } = useAppRuntime();
  return useMemo(() => {
    const fixedT: ReturnType<RuntimeI18n["getFixedT"]> = i18n
      ? i18n.getFixedT(null, namespace)
      : FALLBACK_I18N.getFixedT(null, namespace);
    return (key: string, vars: MessageVars = {}) => {
      const result = fixedT(key, vars);
      return typeof result === "string" ? result : String(result);
    };
  }, [i18n, namespace]);
}

/**
 * A namespaced translator with a bundled-English `fallback`: resolves a key
 * against the host runtime's merged i18n for `namespace`, with native plural
 * defaults declared in `fallback`, then the key. For counted keys, `_other`
 * supplies the default when the bundle omits the locale's selected category;
 * zero gets a special default only when `_zero` is declared.
 * The translate-with-fallback owner
 * — the UI namespace hook and each addon's `useXT` build on it — so a
 * component renders its English even before its runtime bundle is mounted
 * (unit tests, storybook, provider-less embeds). Stable identity (memoized on
 * the namespace translator) for use in dependency arrays.
 */
export function useNamespaceT(
  namespace: string,
  fallback: MessageResources,
): (key: string, vars?: MessageVars) => string {
  const t = useT(namespace);
  return useCallback(
    (key: string, vars: MessageVars = {}) => {
      const defaultValue = (typeof vars.count === "number" ? fallback[`${key}_other`] : undefined)
        ?? fallback[key] ?? key;
      const pluralDefaults = Object.fromEntries(
        PLURAL_SUFFIXES.flatMap((suffix) => {
          const value = fallback[`${key}_${suffix}`];
          return value === undefined ? [] : [[`defaultValue_${suffix}`, value]];
        }),
      );
      return t(key, { ...vars, defaultValue, ...pluralDefaults });
    },
    [t, fallback],
  );
}
