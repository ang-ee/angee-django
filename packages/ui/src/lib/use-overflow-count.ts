import * as React from "react";

/**
 * Count the ordered entries that fit, reserving an overflow control when needed.
 * The inert measurement row contains the leading control, one child per entry,
 * then the overflow control, with the same gap as the visible row. Keep it at
 * intrinsic width so moving entries into a popup cannot change measurements.
 * `keepIndex` reserves the width of an entry swapped into the last visible slot.
 */
export function useOverflowCount<T>(
  entries: readonly T[],
  keepIndex = -1,
): [React.RefCallback<HTMLElement>, React.RefCallback<HTMLDivElement>, number] {
  const [container, setContainer] = React.useState<HTMLElement | null>(null);
  const [measurement, setMeasurement] = React.useState<HTMLDivElement | null>(null);
  const [count, setCount] = React.useState<number | null>(null);

  React.useLayoutEffect(() => {
    if (!container || !measurement) return;
    let available = container.getBoundingClientRect().width;
    const measure = () => {
      const widths = Array.from(measurement.children, (child) => child.getBoundingClientRect().width);
      // Unlaid-out hosts (including SSR/test DOMs) cannot supply useful widths.
      if (widths.length !== entries.length + 2 || widths.every((width) => width === 0)) return;
      const leading = widths[0]!;
      const overflow = widths.at(-1)!;
      const gap = parseFloat(getComputedStyle(measurement).columnGap) || 0;
      const itemWidths = widths.slice(1, -1);
      const prefix = [0];
      for (const width of itemWidths) prefix.push(prefix.at(-1)! + width);
      if (leading + prefix.at(-1)! + gap * entries.length <= available) {
        setCount(entries.length);
        return;
      }
      for (let next = entries.length - 1; next > 0; next--) {
        const swapped = keepIndex >= next && keepIndex < entries.length
          ? itemWidths[keepIndex]! - itemWidths[next - 1]! : 0;
        if (leading + prefix[next]! + swapped + overflow + gap * (next + 1) <= available) {
          setCount(next);
          return;
        }
      }
      setCount(0);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((changes) => {
      const change = changes.find((entry) => entry.target === container);
      available = change?.contentRect.width ?? container.getBoundingClientRect().width;
      measure();
    });
    observer.observe(container);
    observer.observe(measurement);
    for (const child of measurement.children) observer.observe(child);
    return () => observer.disconnect();
  }, [container, measurement, entries, keepIndex]);

  return [setContainer, setMeasurement, Math.min(count ?? entries.length, entries.length)];
}
