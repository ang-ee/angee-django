import { holdsPermission } from "@angee/metadata";
import {
  RecordActionBar, useActionResultRun, useRecordChromeActionOutcome,
  type ActionDescriptor,
} from "@angee/ui";
import type { ReactElement } from "react";

import { DecideNeedAccessDocument } from "./documents";
import { useIntakeT } from "./i18n";
import { NEED_MODEL } from "./resources";
import type { CurrentAccessRow } from "./TaskAccessDecisions";

/** The card uses the same Need decision verbs and revision as the former record menu. */
export function TaskAccessActions({ need }: { need: CurrentAccessRow }): ReactElement | null {
  const t = useIntakeT();
  const [decide] = useRecordChromeActionOutcome({
    document: DecideNeedAccessDocument, resultField: "decide_need_access", idArgument: "need",
  }, { invalidateModels: [NEED_MODEL, "decisions.Decision"] });
  const [reset] = useRecordChromeActionOutcome("reset_need_access", {
    idArgument: "need", invalidateModels: [NEED_MODEL, "decisions.Decision"],
  });
  const settle = useActionResultRun();
  if (!holdsPermission(need, "write")) return null;
  const current = need.access_decision;
  const actions: ActionDescriptor[] = current?.is_open ? (["INTAKE_APPROVE", "INTAKE_DENY"] as const).map((action) => ({
    id: `access-${action}-${need.id}`,
    label: t(action === "INTAKE_APPROVE" ? "access.approve" : "access.deny"),
    permission: "write", placement: "toolbar", primary: action === "INTAKE_APPROVE", danger: action === "INTAKE_DENY",
    run: async () => {
      await settle(() => decide(need.id, { action, expected_revision: need.revision }));
    },
  })) : current ? [{
    id: `access-reset-${need.id}`,
    label: t("access.reset"),
    // Removing access is destructive: the shared action owner confirms it in the danger tone.
    permission: "write", placement: "toolbar", danger: true,
    confirm: { title: t("access.resetTitle"), body: t("access.resetBody") },
    run: async () => {
      await settle(() => reset(need.id, { confirmed: true, expected_revision: need.revision }));
    },
  }] : [];
  return <RecordActionBar record={need} actions={actions} />;
}
