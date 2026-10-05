import * as React from "react";
import type { AnyRouter, HistoryState, LinkOptions } from "@tanstack/react-router";

export type InAppNavigator = (
  href: string,
  options?: { state?: HistoryState },
) => void;

/** Loads an in-app href's route ahead of a likely click; it never navigates. */
export interface InAppPreloader {
  preload: (href: string) => void;
  /** How long a pointer must rest on a link before its route preloads, in milliseconds. */
  delay: number;
}

/**
 * An in-app href as router location options, its query parsed by the host's
 * search codec. Mirrors the href branch of TanStack's buildAndCommitLocation, so
 * paths that take no `href` (preloads, redirects) reach the same location.
 */
export function hrefLocation(router: AnyRouter, href: string): Pick<LinkOptions, "to" | "search" | "hash"> {
  const url = new URL(href, router.origin);
  return { to: url.pathname, search: router.options.parseSearch(url.search), hash: url.hash.slice(1) };
}

/** Bind the host router once; href navigation preserves its native search codec. */
export function routerNavigator(router: AnyRouter): InAppNavigator {
  return (href, options) => { void router.navigate({ href, ...options }); };
}

/** The router's own intent policy (`defaultPreload`, `defaultPreloadDelay`) for in-app anchors. */
export function routerPreloader(router: AnyRouter): InAppPreloader | undefined {
  if (router.options.defaultPreload !== "intent") return undefined;
  return {
    delay: router.options.defaultPreloadDelay ?? 0,
    preload: (href) => {
      router.preloadRoute(hrefLocation(router, href)).catch((error: unknown) => console.warn(error));
    },
  };
}

interface InAppLinks {
  navigate: InAppNavigator;
  preloader: InAppPreloader | undefined;
}

const InAppLinkContext = React.createContext<InAppLinks | undefined>(undefined);

/**
 * The host supplies routing and preloading; without a navigator, links retain
 * native anchor behavior. A nested provider that only changes how links navigate
 * keeps the enclosing preloader.
 */
export function InAppLinkProvider({ navigate, preload, children }: {
  navigate: InAppNavigator;
  preload?: InAppPreloader;
  children: React.ReactNode;
}): React.ReactElement {
  const enclosing = React.useContext(InAppLinkContext);
  const preloader = preload ?? enclosing?.preloader;
  const value = React.useMemo(() => ({ navigate, preloader }), [navigate, preloader]);
  return <InAppLinkContext.Provider value={value}>{children}</InAppLinkContext.Provider>;
}

export function useInAppNavigator(): InAppNavigator | undefined {
  return React.useContext(InAppLinkContext)?.navigate;
}

export interface InAppLinkHandlers {
  onClick: React.MouseEventHandler<HTMLElement>;
  onMouseEnter: React.MouseEventHandler<HTMLElement>;
  onMouseLeave: React.MouseEventHandler<HTMLElement>;
  onFocus: React.FocusEventHandler<HTMLElement>;
  onBlur: React.FocusEventHandler<HTMLElement>;
  onTouchStart: React.TouchEventHandler<HTMLElement>;
}

/** The in-app destination an anchor follows, or undefined when the browser keeps it. */
function inAppDestination(anchor: HTMLElement, href: string | undefined): string | undefined {
  const destination = href ?? anchor.getAttribute("href") ?? undefined;
  const target = anchor.getAttribute("target");
  if (!destination?.startsWith("/") || destination.startsWith("//")
    || (target && target !== "_self")
    || anchor.getAttribute("rel")?.split(/\s+/).includes("external")
    || anchor.hasAttribute("download")
    || anchor.getAttribute("aria-disabled") === "true") return undefined;
  return destination;
}

/**
 * One activation policy for anchors, including callbacks that accompany following
 * a record: a plain primary click routes, and hover, focus or touch preloads the
 * route under the host router's intent policy. The anchor's own handlers run
 * first. Mark server-served root-relative URLs (admin, media, logout) with
 * rel="external" to retain document navigation.
 */
export function useInAppLink(
  href: string | undefined,
  handlers: Partial<InAppLinkHandlers> = {},
  options?: { navigate?: InAppNavigator; onFollow?: () => void },
): InAppLinkHandlers {
  const links = React.useContext(InAppLinkContext);
  const navigate = options?.navigate ?? links?.navigate;
  const preloader = links?.preloader;
  const pending = React.useRef<ReturnType<typeof setTimeout>>(undefined);
  React.useEffect(() => () => clearTimeout(pending.current), []);
  const preload = (anchor: HTMLElement, delay = preloader?.delay ?? 0) => {
    const destination = preloader && inAppDestination(anchor, href);
    if (!destination || pending.current !== undefined) return;
    if (delay <= 0) return preloader.preload(destination);
    pending.current = setTimeout(() => {
      pending.current = undefined;
      preloader.preload(destination);
    }, delay);
  };
  const cancel = () => {
    clearTimeout(pending.current);
    pending.current = undefined;
  };
  return {
    onClick: (event) => {
      handlers.onClick?.(event);
      const destination = inAppDestination(event.currentTarget, href);
      if (!destination || event.defaultPrevented || event.button !== 0
        || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      options?.onFollow?.();
      if (!navigate) return;
      event.preventDefault();
      navigate(destination);
    },
    onMouseEnter: (event) => { handlers.onMouseEnter?.(event); preload(event.currentTarget); },
    onFocus: (event) => { handlers.onFocus?.(event); preload(event.currentTarget); },
    onTouchStart: (event) => { handlers.onTouchStart?.(event); preload(event.currentTarget, 0); },
    onMouseLeave: (event) => { handlers.onMouseLeave?.(event); cancel(); },
    onBlur: (event) => { handlers.onBlur?.(event); cancel(); },
  };
}
