// The rendered binding's runtime contracts. The DAG owner of the app-runtime
// registry (`AppRuntime` + its `useWidget`/`useSlot`/`usePreviews`/`useT` lookups),
// the context factory (`makeContext`), and the contribution contracts the render
// surfaces consume (menus, slots, previews, widgets, forms). `@angee/app` mounts
// the provider and builds addon manifests against these contracts.

export { makeContext, type ContextBinding } from "./make-context";
export {
  AppRuntimeProvider,
  useAppRuntime,
  useWidget,
  useFormOverride,
  useResourceRoute,
  useResourceRecordHref,
  useResourceRecordHrefLookup,
  useRouteHref,
  useLoginPath,
  useRuntimeAuth,
  useRuntimeLogoutAction,
  useRuntimeUserPreferences,
  useSlot,
  useModelSlot,
  usePreviews,
  useDrawers,
  useChatterRoutes,
  useT,
  useNamespaceT,
  type AppRuntime,
  type RuntimeAuthState,
  type RuntimeAuthUser,
  type RuntimeI18n,
  type RuntimeLogoutAction,
  type ResourceRecordHrefLookup,
  type RuntimeResourceRoutes,
  type RuntimeUserPreferences,
  type RuntimeUserPreferencesState,
  DEFAULT_LOGIN_PATH,
} from "./runtime";
export {
  createRouteHref,
  routeParameterName,
  routeSearchString,
  UnknownRouteError,
  type RouteHref,
  type RouteHrefParams,
  type RouteHrefSearch,
  type RouteHrefSearchValue,
  type RuntimeRouteDescriptor,
} from "./route-href";
export { isModelScopedSlot } from "./contracts";
export type {
  ChatterContribution,
  ChatterRoute,
  ChatterView,
  ChatterViewContext,
  ComposedMenuItem,
  DrawerContribution,
  DrawerEdge,
  FormOverrideMap,
  MenuItem,
  ModelSlotTarget,
  PreviewContribution,
  SlotContribution,
  WidgetMap,
} from "./contracts";
