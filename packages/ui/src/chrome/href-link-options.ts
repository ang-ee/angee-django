import { useMemo } from "react";
import { useRouter, type LinkOptions } from "@tanstack/react-router";

/** Chrome's native Router links use the host's search codec, never a query as a pathname. */
export function useHrefLinkOptions(href: string | undefined): Pick<LinkOptions, "to" | "search" | "hash"> {
  const router = useRouter();
  return useMemo(() => {
    if (href === undefined) return { to: "." };
    if (!href.startsWith("/") || href.startsWith("//")) return { to: href };
    // Mirrors the href branch of TanStack's buildAndCommitLocation.
    const url = new URL(href, router.origin);
    return { to: url.pathname, search: router.options.parseSearch(url.search), hash: url.hash.slice(1) };
  }, [router, href]);
}
