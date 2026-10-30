import { useAuthoredQuery } from "@angee/refine";
import type { ChatterViewContext } from "@angee/ui";
import * as React from "react";

import { RecordSourceThreadsDocument, THREAD_ATTACHMENT_MODEL } from "./documents";

const MODELS = [THREAD_ATTACHMENT_MODEL, "messaging.Thread"] as const;

/** Source-edge authorization belongs to the record_source_threads read. */
export function useRecordSourceThreads(context: ChatterViewContext) {
  const modelLabel = context.route?.canonicalLabel ?? context.route?.modelLabel ?? "";
  const recordId = context.view.kind === "record" ? context.view.sqid ?? "" : "";
  const variables = React.useMemo(() => ({ modelLabel, recordId }), [modelLabel, recordId]);
  return useAuthoredQuery(RecordSourceThreadsDocument, variables, {
    enabled: Boolean(modelLabel && recordId), models: MODELS,
  });
}
