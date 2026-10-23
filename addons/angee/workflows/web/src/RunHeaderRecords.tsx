import { RecordReference, textRoleVariants } from "@angee/ui";
import { useAuthoredQuery } from "@angee/refine";
import { useMemo, type ReactElement } from "react";

import { RUN_MODEL, RUN_MODELS, RunRelatedDocument } from "./documents.console";
import { useWorkflowsT } from "./i18n";
import { runRecords } from "./related";

/** The run's source and result in its header: what it started from and what it produced. */
export function RunHeaderRecords({ runId }: { runId: string }): ReactElement | null {
  const t = useWorkflowsT();
  const variables = useMemo(() => ({ run: runId }), [runId]);
  const query = useAuthoredQuery(RunRelatedDocument, variables, {
    enabled: Boolean(runId), models: [...RUN_MODELS, "decisions.Decision"], records: [{ model: RUN_MODEL, id: runId }],
  });
  const records = runRecords(query.data);
  const sources = records.filter((record) => record.role === "source");
  const results = records.filter((record) => record.role === "created" || record.role === "changed");
  if (!sources.length && !results.length) return null;
  const links = (items: typeof records) => items.map((record, index) =>
    <span key={`${record.model}:${record.id}`}>{index ? ", " : ""}<RecordReference model={record.model} id={record.id} /></span>);
  return <span className={textRoleVariants({ role: "meta" })}>
    {sources.length ? <span>{t("related.role.source")}: {links(sources)}</span> : null}
    {sources.length && results.length ? " · " : null}
    {results.length ? <span>{t("run.result")}: {links(results)}</span> : null}
  </span>;
}
