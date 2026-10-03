import * as React from "react";

/**
 * Count the ordered entries that fit, reserving an overflow control when needed.
 * The inert measurement row contains the leading control, one child per entry,
 * then the overflow control. The container owns the visible row's gap. Keep it at
 * intrinsic width so moving entries into a popup cannot change measurements.
 * `keepIndex` takes the last visible slot when it would otherwise overflow.
 */
export function useOverflowCount<T>(
  entries: readonly T[],
  keepIndex = -1,
): [React.RefCallback<HTMLElement>, React.RefCallback<HTMLDivElement>, readonly number[], readonly number[]] {
  const [container, setContainer] = React.useState<HTMLElement | null>(null);
  const [measurement, setMeasurement] = React.useState<HTMLDivElement | null>(null);
  const [count, setCount] = React.useState<number | null>(null);

  React.useLayoutEffect(() => {
    if (!container || !measurement) return;
    const measure = () => {
      // Read border boxes on every measurement, including observer callbacks.
      const style = getComputedStyle(container);
      const inset = [style.borderLeftWidth, style.borderRightWidth, style.paddingLeft, style.paddingRight]
        .reduce((sum, value) => sum + (parseFloat(value) || 0), 0);
      const available = container.getBoundingClientRect().width - inset;
      const widths = Array.from(measurement.children, (child) => child.getBoundingClientRect().width);
      // Unlaid-out hosts (including SSR/test DOMs) cannot supply useful widths.
      if (widths.length !== entries.length + 2 || widths.every((width) => width === 0)) return;
      const leading = widths[0]!;
      const overflow = widths.at(-1)!;
      const gap = parseFloat(style.columnGap) || 0;
      const itemWidths = widths.slice(1, -1);
      if (leading + itemWidths.reduce((sum, width) => sum + width, 0) + gap * entries.length <= available) {
        setCount(entries.length);
        return;
      }
      for (let next = entries.length - 1; next > 0; next--) {
        const visibleWidth = visibleEntryIndices(entries.length, next, keepIndex)
          .reduce((sum, index) => sum + itemWidths[index]!, 0);
        if (leading + visibleWidth + overflow + gap * (next + 1) <= available) {
          setCount(next);
          return;
        }
      }
      setCount(0);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(container, { box: "border-box" });
    observer.observe(measurement, { box: "border-box" });
    for (const child of measurement.children) observer.observe(child, { box: "border-box" });
    return () => observer.disconnect();
  }, [container, measurement, entries, keepIndex]);

  const visible = visibleEntryIndices(entries.length, Math.min(count ?? entries.length, entries.length), keepIndex);
  const overflow = Array.from({ length: entries.length }, (_, index) => index).filter((index) => !visible.includes(index));
  return [setContainer, setMeasurement, visible, overflow];
}

function visibleEntryIndices(total: number, count: number, keepIndex: number): number[] {
  const visible = Array.from({ length: count }, (_, index) => index);
  if (count > 0 && keepIndex >= count && keepIndex < total) visible[count - 1] = keepIndex;
  return visible;
}
