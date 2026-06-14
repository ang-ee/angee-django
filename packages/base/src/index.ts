// @angee/base — the single rendered binding for @angee/sdk. Styled primitives,
// data-bound views, app shell, and the auth surface, composed onto base-ui +
// tailwind-variants. The SDK stays headless; rendering lives here.

// Styling foundation + the drag-and-drop seam.
export {
  cn,
  tv,
  ANGEE_TW_MERGE_CONFIG,
  type VariantProps,
  DND_MIME,
  writeDndPayload,
  readDndPayload,
  dragHasAcceptedType,
  useDraggable,
  useDropTarget,
  type DndPayload,
  type UseDropTargetOptions,
} from "./lib";

// The stack's debounce owner (docs/stack.md), re-exported so addons reuse it
// without re-declaring the dependency.
export { useDebounce, useDebouncedCallback } from "use-debounce";

// i18n — the base namespace translator.
export { useBaseT, enBaseMessages } from "./i18n";

// App composition root.
export {
  createApp,
  PassthroughChrome,
  type AngeeApp,
  type BaseAddon,
  type BaseAddonRoute,
  type CreateAppInput,
  type ShellConfig,
} from "./createApp";
export {
  breadcrumbItemsFromMatches,
  routeChromeFromMatches,
  useRouteBreadcrumbItems,
  useRouteChrome,
  type BreadcrumbItem,
  type RouteBreadcrumbFactory,
  type RouteChromeStaticData,
} from "./route-static-data";

// Data-bound views.
export * from "./views";
export {
  GraphView,
  type GraphViewEdge,
  type GraphViewEdgeStyle,
  type GraphViewLayout,
  type GraphViewNode,
  type GraphViewNodeStyle,
  type GraphViewProps,
} from "./views/GraphView";
export * from "./widgets";
export * from "./feedback";
export * from "./toolbars";
export * from "./page";
export * from "./layouts";
export * from "./fragments";

// App shell (chrome) and auth surface.
export * from "./chrome";
export * from "./communication";
export * from "./shell";
export * from "./auth";
export * from "./preview";

// UI primitives — embeddable, SDK-agnostic atoms on base-ui.
export * from "./ui/button";
export * from "./ui/spinner";
export * from "./ui/field";
export * from "./ui/form";
export * from "./ui/label";
export * from "./ui/input";
export * from "./ui/textarea";
export * from "./ui/select";
export * from "./ui/pager";
export * from "./ui/checkbox";
export * from "./ui/switch";
export * from "./ui/slider";
export * from "./ui/number-field";
export * from "./ui/radio-group";
export * from "./ui/calendar";
export * from "./ui/dialog";
export * from "./ui/alert-dialog";
export * from "./ui/table";
export * from "./ui/tabs";
export * from "./ui/accordion";
export * from "./ui/collapsible";
export * from "./ui/drawer";
export * from "./ui/toggle";
export * from "./ui/toggle-group";
export * from "./ui/badge";
export * from "./ui/chip";
export * from "./ui/avatar";
export * from "./ui/status-icon";
export * from "./ui/kbd";
export * from "./ui/code";
export * from "./ui/alert";
export * from "./ui/text-link";
export * from "./ui/nav-link";
export * from "./ui/section-eyebrow";
export * from "./ui/card";
export * from "./ui/separator";
export * from "./ui/popover";
export * from "./ui/dropdown-menu";
export * from "./ui/context-menu";
export * from "./ui/navigation-menu";
export * from "./ui/command";
export * from "./ui/selection-bar";
export * from "./ui/tooltip";
export * from "./ui/scroll-area";
export * from "./ui/toolbar";
export * from "./ui/form-layout";
