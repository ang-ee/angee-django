import type { ReactElement } from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Button, Column, EmptyState, ErrorBanner, List, LoadingPanel, MetaGrid, MetaSection,
  Page, PageBody, RecordHeader, ResourceList, useRouteParam,
} from "@angee/ui";

import { WORKFLOW_MODEL, WORKFLOW_VERSION_MODEL, WorkflowDocument } from "./catalogue/documents.console";
import { useWorkflowsT } from "./i18n";
import { RunsList } from "./RunsPage";

export function WorkflowPage(): ReactElement {
  const id = useRouteParam("id");
  const t = useWorkflowsT();
  const query = useAuthoredQuery(WorkflowDocument, { id: id ?? "" }, { enabled: Boolean(id),
    models: [WORKFLOW_MODEL, WORKFLOW_VERSION_MODEL], records: id ? [{ model: WORKFLOW_MODEL, id }] : [],
    relatedModels: [WORKFLOW_VERSION_MODEL],
  });
  const workflow = query.data?.workflow_by_pk;
  if (query.isLoading) return <LoadingPanel />;
  if (!workflow) return query.error
    ? <ErrorBanner description={t("catalogue.error")} actions={<Button onClick={() => void query.refetch()}>{t("catalogue.reload")}</Button>} />
    : <EmptyState title={t("catalogue.notFound")} />;
  return <Page>
    <RecordHeader title={workflow.name} description={workflow.description} />
    <PageBody className="space-y-6">
      <MetaGrid rows={[
        [t("catalogue.key"), workflow.key], [t("catalogue.subjectModel"), workflow.subject_model],
        [t("catalogue.publishedVersion"), workflow.published?.number],
      ]} />
      <MetaSection headingLevel={2} title={t("catalogue.versions")}>
        <ResourceList resource={WORKFLOW_VERSION_MODEL} hideCreate presentation="embedded"
          baseFilter={{ workflow: { exact: workflow.id } }} order={{ number: "DESC" }} emptyContent={t("catalogue.noVersions")}>
          <List resource={WORKFLOW_VERSION_MODEL}>
            <Column field="number" header={t("catalogue.version")} />
            <Column field="created_at" header={t("catalogue.created")} />
            <Column field="published_by" header={t("catalogue.publishedBy")} />
            <Column field="content_hash" header={t("catalogue.contentHash")} />
          </List>
        </ResourceList>
      </MetaSection>
      <MetaSection headingLevel={2} title={t("catalogue.recentRuns")}><RunsList embedded baseFilter={{ workflow: { exact: workflow.id } }} /></MetaSection>
    </PageBody>
  </Page>;
}
