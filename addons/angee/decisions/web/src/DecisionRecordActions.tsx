import type { ActionFieldName } from "@angee/gql/console/actions";
import { holdsPermission } from "@angee/metadata";
import {
  RecordActionBar,
  useActionResultRun,
  useAppRuntime,
  useRecordChromeActionOutcome,
  useRecordChromeContext,
  useUiT,
  type ActionDescriptor,
} from "@angee/ui";
import { jsonSchemaActionArgs } from "@angee/ui/views/json-schema";
import type { ReactElement } from "react";

import { useDecisionsT } from "./i18n";
import { DECISION_MODEL } from "./resources";

/** Decision and successor admission compose the record action owner. */
export function DecisionRecordActions(): ReactElement | null {
  const { record } = useRecordChromeContext();
  const { widgets } = useAppRuntime();
  const t = useDecisionsT();
  const uiT = useUiT();
  const [decide] = useRecordChromeActionOutcome<ActionFieldName>("decide_human_decision", {
    invalidateModels: [DECISION_MODEL],
  });
  const [revisit] = useRecordChromeActionOutcome<ActionFieldName>("revisit_human_decision", {
    invalidateModels: [DECISION_MODEL],
  });
  const settle = useActionResultRun({ linkTo: DECISION_MODEL });
  if (!record || !holdsPermission(record, "act")) return null;
  const actions: ActionDescriptor[] = [{
    id: "decide",
    label: t("decision.submit"),
    placement: "toolbar",
    primary: true,
    visibleWhen: (row) => row.is_open === true,
    args: (context) => jsonSchemaActionArgs(context.record?.form_schema, widgets, {
      initialValues: context.record?.resolution,
      translate: uiT,
    }),
    submit: (values, context) => {
      const id = context.record?.id;
      if (typeof id !== "string" || typeof context.record?.revision !== "number") return null;
      return decide(id, {
        revision: context.record.revision,
        action: values.action,
        values,
      });
    },
  }];
  actions.push({
    id: "revisit",
    label: t("decision.revisit"),
    visibleWhen: (row) => row.can_revisit === true,
    confirm: { title: t("decision.revisit.title"), body: t("decision.revisit.body") },
    run: async (context) => {
      const id = context.record?.id;
      if (typeof id === "string") await settle(() => revisit(id, { revision: context.record?.revision }));
    },
  });
  return <RecordActionBar record={record} actions={actions} />;
}
