import type { ActionFieldName } from "@angee/gql/console/actions";
import { holdsPermission } from "@angee/metadata";
import {
  ErrorBanner,
  RecordActionBar,
  formSpecBranches,
  useActionResultRun,
  useAppRuntime,
  useRecordChromeActionOutcome,
  useRecordChromeContext,
  type ActionDescriptor,
} from "@angee/ui";
import type { ReactElement } from "react";

import { useDecisionsT } from "./i18n";
import { DECISION_MODEL } from "./resources";

/** Frozen branches and successor admission compose the record action owner. */
export function DecisionRecordActions(): ReactElement | null {
  const { record } = useRecordChromeContext();
  const { widgets } = useAppRuntime();
  const t = useDecisionsT();
  const [decide] = useRecordChromeActionOutcome<ActionFieldName>("decide_human_decision", {
    invalidateModels: [DECISION_MODEL],
  });
  const [revisit] = useRecordChromeActionOutcome<ActionFieldName>("revisit_human_decision", {
    invalidateModels: [DECISION_MODEL],
  });
  const settle = useActionResultRun({ linkTo: DECISION_MODEL });
  if (!record || !holdsPermission(record, "act")) return null;
  let branches: ReturnType<typeof formSpecBranches> = [];
  try {
    if (record.is_open === true) branches = formSpecBranches(record.form_schema, widgets);
  } catch {
    return <ErrorBanner description={t("decision.schemaError")} />;
  }
  const actions: ActionDescriptor[] = branches.map((branch) => ({
    id: `decide-${branch.value}`,
    label: branch.label,
    args: [{ name: "values", argKind: "formSpec", fields: branch.fields }],
    submit: (values, context) => {
      const id = context.record?.id;
      if (typeof id !== "string" || typeof context.record?.revision !== "number") return null;
      return decide(id, {
        revision: context.record.revision,
        action: branch.value,
        values: values.values,
      });
    },
  }));
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
