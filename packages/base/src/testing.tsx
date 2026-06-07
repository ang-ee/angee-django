// Public test helpers for rendered Angee apps. These utilities mount the real
// createApp provider/router stack while keeping network and shell chrome
// assertions hermetic.

import { createElement, useEffect, type ReactNode } from "react";
import { waitFor } from "@testing-library/react";
import {
  useMenus,
  type AngeeUrqlClientOptions,
  type ComposedMenuItem,
} from "@angee/sdk";
import type { Root } from "react-dom/client";

import {
  PassthroughChrome,
  createApp,
  type BaseAddon,
  type CreateAppInput,
} from "./createApp";
import {
  useRouteBreadcrumbItems,
  useRouteChrome,
  type BreadcrumbItem,
  type RouteChromeStaticData,
} from "./route-static-data";

/** Captured shell chrome plus runtime menus from a mounted route. */
export interface CapturedChromeProps extends RouteChromeStaticData {
  /** The rendered trail: static route crumbs plus dynamic crumb factories. */
  trail: readonly BreadcrumbItem[];
  menus: readonly ComposedMenuItem[];
}

/** Serializable chrome assertion shape used by addon chrome pins. */
export interface ChromeSnapshot
  extends Pick<RouteChromeStaticData, "icon" | "title"> {
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
  schemas?: Record<string, AngeeUrqlClientOptions>;
}

/** Mount createApp and capture chrome after React commits the active shell. */
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
    const chrome = useRouteChrome();
    const trail = useRouteBreadcrumbItems();
    const menus = useMenus();
    useEffect(() => {
      captures.push({
        ...chrome,
        breadcrumbs: trail,
        trail,
        menus,
      });
    }, [chrome, menus, trail]);
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
    shells: {
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
    title: props.title,
    icon: props.icon,
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
        data: { __typename: "Query", currentUser: null },
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    ),
  );
}
