import type { ResourceFilter } from "@angee/metadata";
import {
  Column, Field, Form, List, ListView, ResourceList, Tab,
  formViewSectionsSlot, useRecordChromeContext,
} from "@angee/ui";
import type { ReactElement } from "react";

import { useDecisionsT } from "./i18n";
import { DECISION_MODEL } from "./resources";

/** One prop-driven decision pane for a resource or a filtered subject. */
export function DecisionsList({ baseFilter, routed = false }: { baseFilter?: ResourceFilter<string>; routed?: boolean }): ReactElement {
  const t = useDecisionsT();
  if (!routed) return <ListView resource={DECISION_MODEL} presentation="embedded" scope="local"
    fields={["id", "kind", "verdict", "resolved_at"]} baseFilter={baseFilter}
    order={{ created_at: "DESC" }} columns={[
      { field: "kind", header: t("decisions.kind") },
      { field: "verdict", header: t("decisions.verdict"), widget: "statusBadge" },
      { field: "resolved_at", header: t("decisions.resolved") },
    ]} />;
  return <ResourceList resource={DECISION_MODEL} presentation={routed ? "page" : "embedded"} placement="inline" hideCreate routed={routed}
    baseFilter={baseFilter}>
    <List resource={DECISION_MODEL} order={{ created_at: "DESC" }}>
      <Column field="kind" /><Column field="verdict" widget="statusBadge" /><Column field="resolved_at" />
    </List>
    <Form resource={DECISION_MODEL}>
      <Field name="kind" title readOnly /><Field name="verdict" widget="statusbar" readOnly />
      <Field name="context" widget="json" readOnly /><Field name="resolution" widget="json" readOnly />
      <Field name="resolved_by" readOnly /><Field name="resolved_at" readOnly />
    </Form>
  </ResourceList>;
}

export function RecordDecisions(): ReactElement {
  const { resource, recordId } = useRecordChromeContext();
  return <DecisionsList baseFilter={{ subject_content_type: { exact: resource },
    subject_object_id: { exact: recordId } }} />;
}

function DecisionsLabel(): ReactElement {
  const t = useDecisionsT();
  return <>{t("decisions.label")}</>;
}

/** An addon opts a subject model into the generic Decision.subject tab. */
export function decisionRecordTab(resource: string) {
  return {
    ...formViewSectionsSlot(resource), id: `decisions.subject.${resource}`, sequence: 50,
    content: <Tab id="decisions" label={<DecisionsLabel />}><RecordDecisions /></Tab>,
  };
}
