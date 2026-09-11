import { useAuthoredQuery } from "@angee/refine";
import { Button, EmptyState, LoadingPanel } from "@angee/ui";
import type { ChatterViewContext } from "@angee/ui/runtime";
import * as React from "react";

import { READ_MODELS, RecordSourceThreadsDocument } from "./documents";
import { useMessagingT } from "./i18n";
import { ThreadTranscript } from "./ThreadTranscript";

export interface RecordSourceThreadsPaneProps {
  context: ChatterViewContext;
}

/** Read-only source conversations linked to the current business record. */
export function RecordSourceThreadsPane({ context }: RecordSourceThreadsPaneProps): React.ReactElement {
  const t = useMessagingT();
  const modelLabel = context.route?.modelLabel;
  const recordId = context.view.kind === "record" ? context.view.sqid : undefined;
  const enabled = Boolean(modelLabel && recordId);
  const query = useAuthoredQuery(
    RecordSourceThreadsDocument,
    { modelLabel: modelLabel ?? "", recordId: recordId ?? "" },
    { enabled, models: READ_MODELS },
  );
  const rows = query.data?.record_source_threads ?? [];
  const [selected, setSelected] = React.useState<string | null>(null);
  const selectedId = rows.some((row) => row.thread.id === selected) ? selected : rows[0]?.thread.id;

  if (!enabled) {
    return <EmptyState icon="inbox" title={t("sources.empty")} description={t("sources.emptyHint")} className="min-h-48 p-4" />;
  }
  if (query.isFetching && query.data === undefined) {
    return <LoadingPanel message={t("sources.loading")} />;
  }
  if (query.error) {
    return <EmptyState icon="inbox" title={t("sources.unavailable")} description={t("sources.unavailableHint")} className="min-h-48 p-4" />;
  }
  if (rows.length === 0) {
    return <EmptyState icon="inbox" title={t("sources.empty")} description={t("sources.emptyHint")} className="min-h-48 p-4" />;
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-3">
      {rows.length > 1 ? (
        <div className="flex flex-wrap gap-2">
          {rows.map((row) => (
            <Button key={row.id} variant={row.thread.id === selectedId ? "secondary" : "ghost"} onClick={() => setSelected(row.thread.id)}>
              {row.thread.title?.text || row.label || t("sources.conversation")}
            </Button>
          ))}
        </div>
      ) : null}
      {selectedId ? <ThreadTranscript threadId={selectedId} order="history" /> : null}
    </div>
  );
}
