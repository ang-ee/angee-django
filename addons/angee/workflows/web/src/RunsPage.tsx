import {
  Column, Facet, List, RecordReference, ResourceList,
  useRouteHref, type ResourceListProps, type StringIdRow,
} from "@angee/ui";
import { RUN_MODEL } from "./documents.console";
import { useWorkflowsT } from "./i18n";

export function RunsPage() {
  return <RunsList />;
}

/** Every run collection shares its projection; resource owners retain paging and filter semantics. */
export function RunsList({ baseFilter, embedded = false }: {
  baseFilter?: ResourceListProps["baseFilter"]; embedded?: boolean;
}) {
  const t = useWorkflowsT();
  const href = useRouteHref();
  return <ResourceList<StringIdRow> resource={RUN_MODEL} hideCreate baseFilter={baseFilter}
    presentation={embedded ? "embedded" : undefined} order={{ created_at: "DESC" }}
    fields={["subject_model"]} emptyContent={t("runs.empty")}
    rowHref={(row) => href("workflows.runs.record", { id: row.id })}>
    <List resource={RUN_MODEL}>
      <Facet field="workflow" label={t("run.workflow")} />
      <Column field="status" header={t("run.status")} widget="statusBadge" />
      <Column field="version.workflow.name" header={t("run.workflow")} />
      <Column field="subject_id" header={t("run.subject")} render={(row) => typeof row.subject_id === "string" && typeof row.subject_model === "string"
        ? <RecordReference model={row.subject_model} id={row.subject_id} /> : null} />
      <Column field="origin" header={t("run.origin")} widget="statusBadge" />
      <Column field="created_at" header={t("run.started")} />
      <Column field="finished_at" header={t("run.finished")} />
      <Column field="outcome" header={t("run.outcome")} />
    </List>
  </ResourceList>;
}
