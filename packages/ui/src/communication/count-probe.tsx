import * as React from "react";

import type { ChatterViewContext } from "../runtime";

/** Counts published by independently mounted tab or group readers. */
export function useChatterCounts() {
  const [counts, setCounts] = React.useState<Record<string, number>>({});
  const publish = React.useCallback((id: string, count: number | undefined) => {
    setCounts((current) => {
      if (current[id] === count) return current;
      if (count === undefined) {
        const { [id]: _removed, ...rest } = current;
        return rest;
      }
      return { ...current, [id]: count };
    });
  }, []);
  return [counts, publish] as const;
}

export interface CountProbeProps {
  id: string;
  context: ChatterViewContext;
  onCount: (id: string, count: number | undefined) => void;
}

/** Each contributed hook gets its own React lifetime, even as eligibility changes. */
export function ChatterCountProbe({ id, useCount, context, onCount }: CountProbeProps & {
  useCount: (context: ChatterViewContext) => number | undefined;
}): null {
  const count = useCount(context);
  React.useEffect(() => {
    onCount(id, count);
    return () => onCount(id, undefined);
  }, [id, count, onCount]);
  return null;
}
