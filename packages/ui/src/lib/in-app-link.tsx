import * as React from "react";
import { useRouter, type HistoryState } from "@tanstack/react-router";

export type InAppNavigator = (
  href: string,
  options?: { replace?: boolean; state?: HistoryState },
) => void;

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

/** One activation policy for anchors, including callbacks that accompany following a record. */
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
    if (!destination?.startsWith("/") || destination.startsWith("//")
      || event.defaultPrevented || event.button !== 0
      || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey
      || (target && target !== "_self") || event.currentTarget.hasAttribute("download")) return;
    options?.onFollow?.();
    if (!navigate) return;
    event.preventDefault();
    navigate(destination);
  };
}

/** Chrome's native Router links use the host's search codec, never a query as a pathname. */
export function useHrefLinkOptions(href: string | undefined) {
  const router = useRouter();
  if (href === undefined) return { to: "." };
  if (!href.startsWith("/") || href.startsWith("//")) return { to: href };
  const url = new URL(href, router.origin);
  return { to: url.pathname, search: () => router.options.parseSearch(url.search), hash: url.hash.slice(1) };
}
