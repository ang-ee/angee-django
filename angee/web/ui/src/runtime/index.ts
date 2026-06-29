// The rendered binding's runtime contracts. The DAG owner of the app-runtime
// registry (`AppRuntime` + its `useWidget`/`useSlot`/`usePreviews`/`useT` lookups),
// the context factory (`makeContext`), and the contribution contracts the render
// surfaces consume (menus, slots, previews, widgets, forms). `@angee/app` mounts
// the provider; `@angee/sdk`'s `defineAddon`/`composeAddons` build manifests
// against these contracts.

export { makeContext, type ContextBinding } from "./make-context";
export {
  AppRuntimeProvider,
  useAppRuntime,
  useWidget,
  useFormOverride,
  useResourceRoute,
  useSlot,
  usePreviews,
  useDrawers,
  useChatterRoutes,
  useT,
  useNamespaceT,
  type AppRuntime,
} from "./runtime";
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
  PreviewContribution,
  SlotContribution,
  WidgetMap,
} from "./contracts";
