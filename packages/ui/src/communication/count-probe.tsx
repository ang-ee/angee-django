import * as React from "react";

import type { ChatterViewContext } from "../runtime";

/**
 * Numbers published by independently mounted tab or group readers, each under its id for as long
 * as its publisher is mounted: a tab's or a group's count, or how many blocks a group shows.
 */
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
}): React.ReactElement {
  return <PublishedCount id={id} count={useCount(context)} onCount={onCount} />;
}

/** Publish one number under its id while mounted; only a changed number republishes. */
export function PublishedCount({ id, count, onCount }: Omit<CountProbeProps, "context"> & { count: number | undefined }): null {
  React.useEffect(() => {
    onCount(id, count);
    return () => onCount(id, undefined);
  }, [id, count, onCount]);
  return null;
}
