import { Column, Field, Form, Group, List, ResourceList } from "@angee/ui";

import { WORKFLOW_MODEL, WORKFLOW_VERSION_MODEL } from "./catalogue/resources";
import { useWorkflowsT } from "./i18n";
import { RunsList } from "./RunsPage";

/** Readable workflow identities; publication and authoring remain backend-owned. */
export function WorkflowsPage() {
  const t = useWorkflowsT();
  return <ResourceList resource={WORKFLOW_MODEL} hideCreate placement="inline" routed recordTabs={[
    { id: "versions", label: t("catalogue.versions"), render: ({ recordId }) =>
      <List resource={WORKFLOW_VERSION_MODEL} scope="local" presentation="embedded"
        baseFilter={{ workflow: { exact: recordId } }} order={{ number: "DESC" }} emptyContent={t("catalogue.noVersions")}>
        <Column field="number" header={t("catalogue.version")} />
        <Column field="created_at" header={t("catalogue.created")} />
        <Column field="published_by" header={t("catalogue.publishedBy")} />
        <Column field="content_hash" header={t("catalogue.contentHash")} />
      </List> },
    { id: "runs", label: t("catalogue.recentRuns"), render: ({ recordId }) =>
      <RunsList embedded baseFilter={{ "version.workflow": { exact: recordId } }} /> },
  ]}>
    <List order={{ name: "ASC" }} emptyContent={t("catalogue.empty")}>
      <Column field="key" header={t("catalogue.key")} />
      <Column field="name" header={t("catalogue.name")} />
      <Column field="subject_model" header={t("catalogue.subjectModel")} />
      <Column field="published.number" header={t("catalogue.publishedVersion")} />
    </List>
    <Form readOnly>
      <Field name="name" label={t("catalogue.name")} title />
      <Group columns={2}>
        <Field name="key" label={t("catalogue.key")} />
        <Field name="subject_model" label={t("catalogue.subjectModel")} />
        <Field name="published.number" label={t("catalogue.publishedVersion")} />
      </Group>
      <Field name="description" label={t("catalogue.description")} widget="textarea" />
    </Form>
  </ResourceList>;
}
