import * as React from "react";

/**
 * Observe whether an element meets a minimum width in CSS pixels.
 *
 * ResizeObserver owns container measurements; callers own only the threshold
 * where their composition changes.
 */
export function useContainerQuery<TElement extends Element = HTMLDivElement>(
  minWidth: number,
): [React.RefCallback<TElement>, boolean] {
  const [element, setElement] = React.useState<TElement | null>(null);
  const [matches, setMatches] = React.useState(false);

  React.useLayoutEffect(() => {
    if (element == null) return;

    const update = (width: number) => setMatches(width >= minWidth);
    update(element.getBoundingClientRect().width);

    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) update(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [element, minWidth]);

  return [setElement, matches];
}
