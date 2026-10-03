import * as React from "react";
import type { AnyRouter, HistoryState } from "@tanstack/react-router";

export type InAppNavigator = (
  href: string,
  options?: { state?: HistoryState },
) => void;

/** Bind the host router once; href navigation preserves its native search codec. */
export function routerNavigator(router: AnyRouter): InAppNavigator {
  return (href, options) => { void router.navigate({ href, ...options }); };
}

const InAppLinkContext = React.createContext<InAppNavigator | undefined>(undefined);

/** The host supplies routing; without a navigator, links retain native anchor behavior. */
export function InAppLinkProvider({ navigate, children }: {
  navigate: InAppNavigator;
  children: React.ReactNode;
}): React.ReactElement {
  return <InAppLinkContext.Provider value={navigate}>{children}</InAppLinkContext.Provider>;
}

export function useInAppNavigator(): InAppNavigator | undefined {
  return React.useContext(InAppLinkContext);
}

/**
 * One activation policy for anchors, including callbacks that accompany following a record.
 * Mark server-served root-relative URLs (admin, media, logout) with rel="external"
 * to retain document navigation.
 */
export function useInAppLinkClick(
  href: string | undefined,
  onClick?: React.MouseEventHandler<HTMLElement>,
  options?: { navigate?: InAppNavigator; onFollow?: () => void },
): React.MouseEventHandler<HTMLElement> {
  const inheritedNavigate = useInAppNavigator();
  const navigate = options?.navigate ?? inheritedNavigate;
  return (event) => {
    onClick?.(event);
    const destination = href ?? event.currentTarget.getAttribute("href") ?? undefined;
    const target = event.currentTarget.getAttribute("target");
    const external = event.currentTarget.getAttribute("rel")?.split(/\s+/).includes("external");
    if (!destination?.startsWith("/") || destination.startsWith("//")
      || event.defaultPrevented || event.button !== 0
      || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey
      || (target && target !== "_self") || external || event.currentTarget.hasAttribute("download")) return;
    options?.onFollow?.();
    if (!navigate) return;
    event.preventDefault();
    navigate(destination);
  };
}
