import { useNavigate } from "@tanstack/react-router";
import { useCallback } from "react";

import { safeRedirectPath } from "./safe-redirect";

/**
 * Return the navigation that follows a sign-in: the page URL's safe `next`,
 * else the safe `redirectTo`, else `/`, as a full page load so the app boots
 * with the new session. The login page and every other sign-in method share it.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function useSignInRedirect(redirectTo = "/"): () => void {
  const navigate = useNavigate();
  return useCallback(() => {
    const next =
      typeof window === "undefined"
        ? null
        : new URLSearchParams(window.location.search).get("next");
    const target = safeRedirectPath(next) ?? safeRedirectPath(redirectTo) ?? "/";
    if (typeof window === "undefined") {
      void navigate({ href: target });
      return;
    }
    window.location.assign(target);
  }, [navigate, redirectTo]);
}
