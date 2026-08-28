import { useCallback, useSyncExternalStore } from "react";

/** Tailwind's `lg` boundary, owned here for JS-driven responsive behavior. */
export const LARGE_VIEWPORT_QUERY = "(min-width: 64rem)";

/** Subscribe to one browser media query without mirroring it through effects. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === "undefined") return () => undefined;
      const media = window.matchMedia?.(query);
      media?.addEventListener("change", onChange);
      return () => media?.removeEventListener("change", onChange);
    },
    [query],
  );
  const getSnapshot = useCallback(
    () => typeof window !== "undefined" && Boolean(window.matchMedia?.(query).matches),
    [query],
  );

  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
