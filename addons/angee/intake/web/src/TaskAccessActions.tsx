import { holdsPermission } from "@angee/metadata";
import { useAuthoredQuery } from "@angee/refine";
import {
  ErrorBanner, RecordActionBar, useActionResultRun,
  useRecordChromeActionOutcome, useRecordChromeContext,
  type ActionDescriptor,
} from "@angee/ui";
import type { ReactElement } from "react";

import {
  DecideNeedAccessDocument, TaskAccessNeedsDocument,
} from "./documents";
import { useIntakeT } from "./i18n";
import { NEED_MODEL } from "./resources";

/** Task header actions target each request's own current Need revision. */
export function TaskAccessActions(): ReactElement | null {
  const { record, recordId } = useRecordChromeContext();
  const t = useIntakeT();
  const query = useAuthoredQuery(TaskAccessNeedsDocument, { task: recordId }, {
    models: [NEED_MODEL, "decisions.Decision"],
  });
  const [decide] = useRecordChromeActionOutcome({
    document: DecideNeedAccessDocument, resultField: "decide_need_access", idArgument: "need",
  }, { invalidateModels: [NEED_MODEL, "decisions.Decision"] });
  const [reset] = useRecordChromeActionOutcome("reset_need_access", {
    idArgument: "need", invalidateModels: [NEED_MODEL, "decisions.Decision"],
  });
  const settle = useActionResultRun();
  if (!record || !holdsPermission(record, "write") || !holdsPermission(record, "share")) return null;
  if (query.error) return <ErrorBanner description={t("access.error")} />;
  const needs = query.data?.intake_needs ?? [];
  const actions: ActionDescriptor[] = needs.filter((need) => holdsPermission(need, "write")).flatMap((need) => {
    const name = need.claimed_name || need.id;
    const current = need.access_decision;
    const open = current?.is_open === true;
    const nameAction = (label: string) => `${label} · ${name}`;
    return [
      ...(open ? (["APPROVE", "DENY"] as const).map((action) => ({
        id: `access-${action}-${need.id}`,
        label: nameAction(t(action === "APPROVE" ? "access.approve" : "access.deny")),
        run: async () => {
          await settle(() => decide(need.id, { action, expected_revision: need.revision }));
        },
      })) : []),
      ...(!open && current ? [{
        id: `access-reset-${need.id}`,
        label: nameAction(t("access.reset")),
        confirm: { title: t("access.resetTitle"), body: t("access.resetBody") },
        run: async () => {
          await settle(() => reset(need.id, { confirmed: true, expected_revision: need.revision }));
        },
      }] : []),
    ];
  });
  return <RecordActionBar record={record} actions={actions} />;
}
