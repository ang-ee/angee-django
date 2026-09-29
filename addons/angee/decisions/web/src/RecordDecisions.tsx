import { useAuthoredQuery } from "@angee/refine";
import {
  Column, ErrorBanner, Field, Form, List, LoadingPanel, ResourceList, Tab,
  formViewSectionsSlot, useRecordChromeContext,
} from "@angee/ui";
import type { ReactElement } from "react";

import { SubjectDecisionsDocument } from "./documents";
import { useDecisionsT } from "./i18n";
import { DECISION_MODEL } from "./resources";

/** Standard collection/detail presentation for a server-scoped set of seats. */
export function DecisionsList({ ids, routed = false }: { ids?: readonly string[]; routed?: boolean }): ReactElement {
  return <ResourceList resource={DECISION_MODEL} presentation={routed ? "page" : "embedded"} placement="inline" hideCreate routed={routed}
    {...(ids ? { baseFilter: { id: { in: [...ids] } } } : {})}>
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
  const t = useDecisionsT();
  const { resource, recordId, dataProviderName } = useRecordChromeContext();
  const query = useAuthoredQuery(SubjectDecisionsDocument, { model: resource, id: recordId }, {
    dataProviderName, models: [DECISION_MODEL, resource],
  });
  if (query.error) return <ErrorBanner title={t("decisions.error")} description={query.error.message} />;
  if (!query.data) return <LoadingPanel />;
  return <DecisionsList ids={query.data.subject_decisions.map((decision) => decision.id)} />;
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
