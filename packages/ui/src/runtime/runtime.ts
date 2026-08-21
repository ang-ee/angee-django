import { useCallback, useMemo } from "react";
import type {
  MessageResources,
  MessageVars,
} from "@angee/refine";
import {
  canonicalModelLabelOrNull,
  useSchemaFieldMetadata,
} from "@angee/metadata";

import type {
  ChatterContribution,
  ChatterRoute,
  DrawerContribution,
  DrawerEdge,
  FormOverrideMap,
  ModelSlotTarget,
  PreviewContribution,
  SlotContribution,
  WidgetMap,
} from "./contracts";
import { makeContext } from "./make-context";
import {
  createRouteHref,
  type RouteHref,
} from "./route-href";

export const DEFAULT_LOGIN_PATH = "/login";

/** Route names derived from one resource-tagged collection declaration. */
export interface RuntimeResourceRoutes {
  collection: string;
  record?: {
    name: string;
    param: string;
  };
}

export type ResourceRecordHrefLookup = (
  resource: string,
  id: string,
) => string | undefined;

/**
 * The merged app runtime an app composes once from its addon manifests. The
 * registry lookups (`useWidget` / `useSlot` / `useT`) read from it;
 * there is no separate provider per registry.
 */
export interface AppRuntime {
  widgets: WidgetMap;
  i18n: RuntimeI18n | null;
  auth: RuntimeAuthState;
  logoutAction: RuntimeLogoutAction;
  userPreferences: RuntimeUserPreferencesState;
  icons: Readonly<Record<string, unknown>>;
  forms: FormOverrideMap;
  chatter: readonly ChatterContribution[];
  chatterRoutes: readonly ChatterRoute[];
  slots: readonly SlotContribution[];
  previews: readonly PreviewContribution[];
  drawers: readonly DrawerContribution[];
  /** Composed collection/record route names per resource id. */
  routesByResource: Readonly<Record<string, RuntimeResourceRoutes>>;
  /** Resolve a composed route name to an encoded href. */
  routeHref: RouteHref;
  /** App-owned sign-in destination shared by auth gates and chrome. */
  loginPath: string;
}

export interface RuntimeI18n {
  language?: string;
  getFixedT: (
    lng: string | readonly string[] | null | undefined,
    ns: string,
  ) => (key: string, options?: RuntimeTOptions) => unknown;
}

export type RuntimeUserPreferences = Record<string, unknown>;

export interface RuntimeAuthUser {
  id: string;
  name: string;
  username?: string;
  email?: string;
  roles?: readonly string[];
}

export interface RuntimeAuthState {
  user: RuntimeAuthUser | null;
  status: "anonymous" | "authenticated";
  hasRole: (role: string) => boolean;
}

export interface RuntimeLogoutAction {
  logout: () => Promise<boolean>;
  fetching: boolean;
  error: Error | null;
}

export interface RuntimeUserPreferencesState {
  preferences: RuntimeUserPreferences;
  setPreferences: (preferences: RuntimeUserPreferences) => Promise<void>;
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
  widgets: {},
  i18n: null,
  auth: ANONYMOUS_RUNTIME_AUTH,
  logoutAction: {
    logout: async () => false,
    fetching: false,
    error: null,
  },
  userPreferences: {
    preferences: EMPTY_USER_PREFERENCES,
    setPreferences: async () => undefined,
  },
  icons: {},
  forms: {},
  chatter: [],
  chatterRoutes: [],
  slots: [],
  previews: [],
  drawers: [],
  routesByResource: {},
  routeHref: createRouteHref([]),
  loginPath: DEFAULT_LOGIN_PATH,
};

const RuntimeContext = makeContext<AppRuntime>("AppRuntime");

/** Provide the runtime, filling any unset registry with its empty default. */
export function AppRuntimeProvider(props: {
  runtime: Partial<AppRuntime>;
  children: React.ReactNode;
}): React.ReactNode {
  const { runtime } = props;
  const parent = RuntimeContext.useMaybe();
  const value = useMemo<AppRuntime>(
    () => ({ ...EMPTY_RUNTIME, ...(parent ?? {}), ...runtime }),
    [parent, runtime],
  );
  return RuntimeContext.Provider({ value, children: props.children });
}

/** The merged runtime, or the empty runtime when unprovided. */
export function useAppRuntime(): AppRuntime {
  return RuntimeContext.useMaybe() ?? EMPTY_RUNTIME;
}

/** Look up a contributed widget by id. */
export function useWidget(id: string): unknown {
  return useAppRuntime().widgets[id];
}

/** Look up an addon-registered create-form override for a resource (or undefined). */
export function useFormOverride(resource: string): unknown {
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
): ((id: string) => string | undefined) | undefined {
  const record = useResourceRoutes(resource)?.record;
  const routeHref = useAppRuntime().routeHref;
  return useMemo(
    () =>
      record === undefined
        ? undefined
        : (id: string) => routeHref.maybe(record.name, {
            [record.param]: id,
          }),
    [record, routeHref],
  );
}

/** Resolve any resource-backed record href, degrading when its addon is absent. */
export function useResourceRecordHrefLookup(): ResourceRecordHrefLookup {
  const metadata = useSchemaFieldMetadata();
  const { routesByResource, routeHref } = useAppRuntime();
  return useCallback(
    (resource: string, id: string) => {
      if (!resource || !id) return undefined;
      const canonicalResource = canonicalModelLabelOrNull(
        metadata.resources ?? [],
        resource,
        "resource record route lookup",
      );
      const record = canonicalResource
        ? routesByResource?.[canonicalResource]?.record
        : undefined;
      return record
        ? routeHref.maybe(record.name, { [record.param]: id })
        : undefined;
    },
    [metadata, routeHref, routesByResource],
  );
}

/** The app-composed, fail-fast route href builder. */
export function useRouteHref(): RouteHref {
  return RuntimeContext.use().routeHref;
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

/**
 * The slot entries contributed to one slot, in merged order.
 *
 * Given several slot names, the entries for each are concatenated in name order.
 * Memoize the array you pass; a fresh identity each render recomputes the filter.
 */
export function useSlot(
  slot: string | readonly string[],
): readonly SlotContribution[] {
  const { slots } = useAppRuntime();
  return useMemo(
    () =>
      typeof slot === "string"
        ? slots.filter((entry) => entry.slot === slot)
        : slot.flatMap((key) => slots.filter((entry) => entry.slot === key)),
    [slots, slot],
  );
}

/**
 * Look up contributions addressed to one or more typed model-slot targets.
 * Target order is preserved so a render owner can apply its own specificity
 * policy without reimplementing the slot/model/impl match.
 */
export function useModelSlot(
  target: ModelSlotTarget | readonly ModelSlotTarget[],
): readonly SlotContribution[] {
  const { slots } = useAppRuntime();
  return useMemo(() => {
    const targets: readonly ModelSlotTarget[] = Array.isArray(target)
      ? target as readonly ModelSlotTarget[]
      : [target as ModelSlotTarget];
    return targets.flatMap((candidate) =>
      slots.filter(
        (entry) =>
          entry.slot === candidate.slot
          && entry.model === candidate.model
          && entry.impl === candidate.impl,
      ),
    );
  }, [slots, target]);
}

/** The addon-contributed file-preview renderers, in composed order. */
export function usePreviews(): readonly PreviewContribution[] {
  return useAppRuntime().previews;
}

/** The route metadata Chatter uses to build the active view envelope. */
export function useChatterRoutes(): readonly ChatterRoute[] {
  return useAppRuntime().chatterRoutes ?? [];
}

/**
 * The composed drawer contributions, optionally narrowed to one edge, in merged
 * order. Mirrors `useSlot`: the shell reads `useDrawers("right")` /
 * `useDrawers("bottom")` to render an edge's stripe-tabs and overlay.
 */
export function useDrawers(
  edge?: DrawerEdge,
): readonly DrawerContribution[] {
  const { drawers } = useAppRuntime();
  return useMemo(
    () => (edge ? drawers.filter((drawer) => drawer.edge === edge) : drawers),
    [drawers, edge],
  );
}

/** A translator bound to one namespace; resolves keys against merged i18n. */
export function useT(namespace: string): (key: string, vars?: MessageVars) => string {
  const { i18n } = useAppRuntime();
  return useMemo(() => {
    const fixedT = i18n?.getFixedT(null, namespace);
    return (key: string, vars: MessageVars = {}) => {
      if (!fixedT) return key;
      const result = fixedT(key, vars);
      return typeof result === "string" ? result : String(result);
    };
  }, [i18n, namespace]);
}

/**
 * A namespaced translator with a bundled-English `fallback`: resolves a key
 * against the host runtime's merged i18n for `namespace`, then falls back to
 * `fallback`, then the key. The one owner of the translate-with-fallback pattern
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
      const defaultValue = fallback[key] ?? key;
      const result = t(key, { ...vars, defaultValue });
      return result === key ? interpolateFallback(defaultValue, vars) : result;
    },
    [t, fallback],
  );
}

function interpolateFallback(template: string, vars: MessageVars): string {
  return template.replace(/\{([A-Za-z0-9_]+)\}/g, (match, name: string) => {
    const value = vars[name];
    return value === undefined ? match : String(value);
  });
}
