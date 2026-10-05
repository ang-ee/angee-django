import { useMemo } from "react";
import { useRouter, type LinkOptions } from "@tanstack/react-router";

import { hrefLocation } from "../lib/in-app-link";

/** Chrome's native Router links use the host's search codec, never a query as a pathname. */
export function useHrefLinkOptions(href: string | undefined): Pick<LinkOptions, "to" | "search" | "hash"> {
  const router = useRouter();
  return useMemo(() => {
    if (href === undefined) return { to: "." };
    if (!href.startsWith("/") || href.startsWith("//")) return { to: href };
    return hrefLocation(router, href);
  }, [router, href]);
}
