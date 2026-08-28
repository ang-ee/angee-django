import { useAuthoredQuery } from "@angee/refine";

import {
  WorkCycleContextDocument,
  WorkQueueContextDocument,
} from "./documents";
import { CYCLE_MODEL, QUEUE_MODEL, STAGE_MODEL } from "./resources";

/** Queue facts shared by its route-split projection pages. */
export function useQueueContext(id: string) {
  return useAuthoredQuery(
    WorkQueueContextDocument,
    { id },
    { enabled: Boolean(id), models: [QUEUE_MODEL, STAGE_MODEL] },
  );
}

/** Cycle label/window facts for the cycle-specific projection page. */
export function useCycleContext(id: string) {
  return useAuthoredQuery(
    WorkCycleContextDocument,
    { id },
    { enabled: Boolean(id), models: [CYCLE_MODEL] },
  );
}
