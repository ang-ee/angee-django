// Public test helpers for rendered Angee apps. These utilities mount the real
// createApp provider/router stack while keeping network and layout chrome
// assertions hermetic.

import { createElement, useEffect, type ReactNode } from "react";
import { waitFor } from "@testing-library/react";
import { useBreadcrumb as useRefineBreadcrumb } from "@refinedev/core";
import type { Root } from "react-dom/client";

import {
  PassthroughChrome,
  createApp,
  type BaseAddon,
  type CreateAppInput,
} from "./create-app";
import type { ChromeMenuItem } from "@angee/ui/chrome/menu-tree";
import { useChromeMenuItems } from "@angee/ui/chrome/refine-menu";

interface CapturedBreadcrumbItem {
  label: ReactNode;
  to?: string;
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
