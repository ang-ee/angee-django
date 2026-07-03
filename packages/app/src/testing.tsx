// Public test helpers for rendered Angee apps. These utilities mount the real
// createApp provider/router stack while keeping network and layout chrome
// assertions hermetic.

import { createElement, useEffect, type ReactElement, type ReactNode } from "react";
import { waitFor } from "@testing-library/react";
import { useBreadcrumb as useRefineBreadcrumb } from "@refinedev/core";
import type { Root } from "react-dom/client";
import {
  AppRuntimeProvider,
  ChatterProvider,
  PrimaryPaneProvider,
  baseIcons,
  useChatter,
  usePrimaryPaneContent,
} from "@angee/ui";
import type { AppRuntime } from "@angee/ui/runtime";

import {
  PassthroughChrome,
  createApp,
  type BaseAddon,
  type BaseAddonRoute,
  type CreateAppInput,
} from "./create-app";
import type { BaseMenuItem, ChromeMenuItem } from "@angee/ui/chrome/menu-tree";
import { useChromeMenuItems } from "@angee/ui/chrome/refine-menu";

export interface ShellPageTestProvidersProps {
  children: ReactNode;
  runtime?: Partial<AppRuntime>;
}

export function ShellPageTestProviders({
  children,
  runtime = {},
}: ShellPageTestProvidersProps): ReactElement {
  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons, ...runtime }}>
      <PrimaryPaneProvider>
        <ChatterProvider>{children}</ChatterProvider>
      </PrimaryPaneProvider>
    </AppRuntimeProvider>
  );
}

export function PrimaryPaneTestHost({
  testId = "shell-primary",
}: {
  testId?: string;
}): ReactElement {
  const { node } = usePrimaryPaneContent();
  return <div data-testid={testId}>{node}</div>;
}

export function ChatterTabsTestHost({
  testId = "shell-chatter",
  tabTestId = (id) => `tab-${id}`,
}: {
  testId?: string;
  tabTestId?: (id: string) => string;
}): ReactElement {
  const { content } = useChatter();
  const tabs = content?.tabs ?? [];
  return (
    <div data-testid={testId} data-tab-ids={tabs.map((tab) => tab.id).join(",")}>
      {tabs.map((tab) => (
        <div key={tab.id} data-testid={tabTestId(tab.id)}>
          {tab.label}
          {tab.children}
        </div>
      ))}
    </div>
  );
}

interface CapturedBreadcrumbItem {
  label: ReactNode;
  to?: string;
}

export function expectValidBaseAddon(addon: BaseAddon): void {
  const routes = addon.routes ?? [];
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const resourceOwners = new Map<string, string>();
  for (const route of routes) {
    if (route.resource) {
      const owner = resourceOwners.get(route.resource);
      if (owner) {
        throw new Error(
          `Addon "${addon.id}" resource "${route.resource}" is claimed by both "${owner}" and "${route.name}".`,
        );
      }
      resourceOwners.set(route.resource, route.name);
    }
    if (trailingRouteParamName(route.path) && !route.parent) {
      throw new Error(
        `Addon "${addon.id}" route "${route.name}" is a record route but has no parent.`,
      );
    }
    if (route.parent && !routesByName.has(route.parent)) {
      throw new Error(
        `Addon "${addon.id}" route "${route.name}" references unknown parent "${route.parent}".`,
      );
    }
    if (route.component !== undefined && typeof route.component !== "function") {
      throw new Error(
        `Addon "${addon.id}" route "${route.name}" component must be a function.`,
      );
    }
  }
  for (const item of addon.menus ?? []) {
    assertValidMenuItem(addon.id, item, routesByName);
  }
  for (const iconName of Object.keys(addon.icons ?? {})) {
    if (!isKebabCase(iconName)) {
      throw new Error(`Addon "${addon.id}" icon "${iconName}" must be kebab-case.`);
    }
  }
}

function assertValidMenuItem(
  addonId: string,
  item: BaseMenuItem,
  routesByName: ReadonlyMap<string, BaseAddonRoute>,
): void {
  if (item.route && !routesByName.has(item.route)) {
    throw new Error(
      `Addon "${addonId}" menu item "${item.id ?? item.route}" references unknown route "${item.route}".`,
    );
  }
  if (item.icon && !isKebabCase(item.icon)) {
    throw new Error(`Addon "${addonId}" menu icon "${item.icon}" must be kebab-case.`);
  }
  item.children?.forEach((child) => assertValidMenuItem(addonId, child, routesByName));
}

function trailingRouteParamName(path: string): string | undefined {
  const segment = path.replace(/\/+$/, "").split("/").at(-1);
  return segment?.startsWith("$") ? segment.slice(1) || undefined : undefined;
}

function isKebabCase(value: string): boolean {
  return /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(value);
}

/** Captured layout chrome plus runtime menus from a mounted route. */
export interface CapturedChromeProps {
  /** The rendered trail from refine's resource/router breadcrumb owner. */
  trail: readonly CapturedBreadcrumbItem[];
  menus: readonly ChromeMenuItem[];
}

/** Serializable chrome assertion shape used by addon chrome pins. */
export interface ChromeSnapshot {
  breadcrumbs: { label: ReactNode; to?: string }[];
}

/** Mounted capture result; callers must cleanup when done. */
export interface CapturedChrome {
  root: Root;
  host: HTMLElement;
  props: () => CapturedChromeProps;
  cleanup: () => void;
}

/** Options for mounting a test app at one route. */
export interface CaptureChromeOptions {
  addons: readonly BaseAddon[];
  path: string;
  home?: string;
  schemas?: CreateAppInput["schemas"];
}

/** Mount createApp and capture chrome after React commits the active layout. */
export async function captureChrome({
  addons,
  path,
  home = path,
  schemas = TEST_SCHEMAS,
}: CaptureChromeOptions): Promise<CapturedChrome> {
  const captures: CapturedChromeProps[] = [];
  const host = document.createElement("div");
  document.body.append(host);
  history.replaceState(null, "", path);

  function CaptureChrome(): ReactNode {
    const trail = useRefineBreadcrumb().breadcrumbs.map((item) => ({
      label: item.label,
      ...(item.href ? { to: item.href } : {}),
    }));
    const menus = useChromeMenuItems();
    useEffect(() => {
      captures.push({
        trail,
        menus,
      });
    }, [menus, trail]);
    return createElement(
      "div",
      null,
      createElement("span", null, "Captured chrome"),
      createElement(
        "output",
        { "aria-label": "Captured breadcrumb trail" },
        trail.map((item, index) =>
          createElement("span", { key: index }, item.label),
        ),
      ),
    );
  }

  const root = createApp({
    addons,
    layouts: {
      console: { chrome: CaptureChrome, requireAuth: false },
      public: {
        chrome: PassthroughChrome,
        requireAuth: false,
        schema: "public",
      },
    },
    schemas,
    defaultSchema: "console",
    subscriptionSchema: "console",
    home,
  }).mount(host);

  try {
    await waitFor(() => {
      if (captures.length === 0) {
        throw new Error("captureChrome: no chrome capture committed yet.");
      }
    });
  } catch (error) {
    root.unmount();
    host.remove();
    throw error;
  }

  return {
    root,
    host,
    props: () => captures.at(-1) ?? { trail: [], menus: [] },
    cleanup: () => {
      root.unmount();
      host.remove();
    },
  };
}

/** Return a serializable chrome snapshot for one route. */
export async function chromeSnapshotForRoute(
  options: CaptureChromeOptions,
): Promise<ChromeSnapshot> {
  const captured = await captureChrome(options);
  try {
    return chromeSnapshot(captured.props());
  } finally {
    captured.cleanup();
  }
}

/** Convert captured chrome into the assertion shape used by tests. */
export function chromeSnapshot(props: CapturedChromeProps): ChromeSnapshot {
  return {
    breadcrumbs: props.trail.map((item) => ({
      label: item.label,
      ...(item.to ? { to: item.to } : {}),
    })),
  };
}

/** Hermetic schemas for tests that do not inspect GraphQL payloads. */
export const TEST_SCHEMAS = {
  public: {
    url: "https://example.test/graphql/public/",
    fetch: testGraphQLFetch,
  },
  console: {
    url: "https://example.test/graphql/console/",
    fetch: testGraphQLFetch,
  },
} satisfies CreateAppInput["schemas"];

/** Minimal GraphQL fetch responder for provider setup in tests. */
export function testGraphQLFetch(): Promise<Response> {
  return Promise.resolve(
    new Response(
      JSON.stringify({
        data: { __typename: "Query", current_user: null },
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    ),
  );
}

/**
 * Install a deterministic in-memory `window.localStorage` for happy-dom
 * suites: Vitest's happy-dom global copy does not expose `localStorage`, and
 * the lookup falls through to Node's experimental accessor (undefined).
 * Returns the stub for direct assertions.
 */
export function installTestLocalStorage(): Storage {
  const entries = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return entries.size;
    },
    clear: () => entries.clear(),
    getItem: (key) => entries.get(key) ?? null,
    key: (index) => [...entries.keys()][index] ?? null,
    removeItem: (key) => {
      entries.delete(key);
    },
    setItem: (key, value) => {
      entries.set(key, value);
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: storage,
  });
  return storage;
}
