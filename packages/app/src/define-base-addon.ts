import type { DataProvider as RefineDataProvider } from "@refinedev/core";
import type {
  RouteComponent,
} from "@tanstack/react-router";
import type {
  ComponentType,
  ReactNode,
} from "react";
import type {
  BaseMenuItem,
} from "@angee/ui/chrome/menu-tree";
import type { PreviewProvider } from "@angee/ui/preview/index";
import type {
  AddonManifest,
  AddonRoute,
} from "./define-addon";

/** A route that also carries the page component the chrome renders. */
export interface BaseAddonRoute extends AddonRoute {
  /**
   * The page the chrome renders for this route — the router's own route-component
   * type, so a `lazyRouteComponent(() => import(...))` (carrying `.preload`) drops
   * straight in next to an eager function component.
   */
  component?: RouteComponent;
  /**
   * Menu item id whose trail seeds chrome for routes outside the menu, or
   * disambiguates chrome derivation when multiple menu items target this route.
   */
  menu?: string;
  title?: ReactNode;
  icon?: string;
}

export interface ResourcePageRoutesOptions {
  /** Layout for both routes. Defaults to the rendered addon's console layout. */
  layout?: string;
  /** Menu id that owns the list route chrome. */
  menu?: string;
  /** Route name for the `$id` child. Defaults to `${name}.record`. */
  detailName?: string;
  /** Name of the trailing route param. Defaults to `id`. */
  param?: string;
  /** Menu id that owns the detail route chrome. Defaults to the list route name. */
  detailMenu?: string;
  /** Optional detail component when the child route renders its own page. */
  detailComponent?: RouteComponent;
}

export function resourcePageRoutes(
  name: string,
  path: string,
  component: RouteComponent,
  resource?: string,
  options: ResourcePageRoutesOptions = {},
): readonly BaseAddonRoute[] {
  const layout = options.layout ?? "console";
  const param = options.param ?? "id";
  return [
    {
      name,
      path,
      layout,
      component,
      ...(resource ? { resource } : {}),
      ...(options.menu ? { menu: options.menu } : {}),
    },
    {
      name: options.detailName ?? `${name}.record`,
      path: `${path}/$${param}`,
      layout,
      parent: name,
      ...(options.detailMenu ? { menu: options.detailMenu } : {}),
      ...(options.detailComponent ? { component: options.detailComponent } : {}),
    },
  ];
}

/** An addon manifest whose routes carry their page components. */
export interface BaseAddon
  extends Omit<AddonManifest, "routes" | "menus" | "previews"> {
  routes?: readonly BaseAddonRoute[];
  menus?: readonly BaseMenuItem[];
  /**
   * Full preview renderers. The SDK manifest tracks only the contribution id
   * (for collision detection); the rendered binding owns `PreviewProvider`, so
   * its addon authors the whole renderer here — `PreviewPane` reads it back.
   */
  previews?: readonly PreviewProvider[];
  /**
   * Refine data providers keyed by provider name. The SDK manifest tracks only
   * the name (for collision detection); the rendered binding owns the live
   * `DataProvider`, so its addon authors the whole provider here — `createApp`
   * registers it alongside the schema-named providers.
   */
  dataProviders?: Readonly<Record<string, Required<RefineDataProvider>>>;
}

/**
 * Declare a rendered (base-binding) addon — the one seam every addon's manifest
 * goes through. The rendered analog of the SDK's headless `defineAddon`: it
 * type-checks the literal against {@link BaseAddon} (routes carrying React
 * components) and returns it unchanged, so addons `defineBaseAddon({...})`
 * instead of annotating `const x: BaseAddon = {...}`.
 */
export function defineBaseAddon(addon: BaseAddon): BaseAddon {
  return {
    ...addon,
    routes: addon.routes?.map(normalizeBaseAddonRoute),
  };
}

function normalizeBaseAddonRoute(route: BaseAddonRoute): BaseAddonRoute {
  return {
    layout: "console",
    ...route,
  };
}

/** Props passed from the active route into a refine layout chrome component. */
export interface RefineLayoutChromeProps {
  children: ReactNode;
}

/** A refine layout registered with `createApp`: chrome, auth, and schema policy. */
export interface RefineLayoutConfig {
  /** Chrome wrapping route bodies (e.g. `ConsoleLayout`). */
  chrome?: ComponentType<RefineLayoutChromeProps>;
  /** Gate routes behind sign-in. Defaults to `true` for every layout but `public`. */
  requireAuth?: boolean;
  /**
   * GraphQL schema this layout's routes read from; reads inside the layout inherit
   * that client. Defaults to the app's `defaultSchema`, so the common console
   * surface needs no override and only the public layout pins itself to `public`.
   */
  schema?: string;
}
