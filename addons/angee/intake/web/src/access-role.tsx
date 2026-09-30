import * as React from "react";
import { holdsPermission } from "@angee/metadata";
import { useAuthoredMutation, useAuthoredQuery, extractActionOutcome } from "@angee/refine";
import { useAccessRole, type AccessRoleOwnerProps, type AccessRoleState } from "@angee/iam";
import { useActionResultRun } from "@angee/ui";

import { AdmitNeedRequesterDocument, RemoveNeedRequesterDocument, TaskAccessNeedsDocument } from "./documents";
import { useIntakeT } from "./i18n";
import { NEED_MODEL } from "./resources";

/** A completed Need decision is the Requester's read seat on the target task. */
export function TaskRequesterAccessRole({ targetId, record }: AccessRoleOwnerProps): null {
  const t = useIntakeT();
  const query = useAuthoredQuery(TaskAccessNeedsDocument, { task: targetId }, {
    dataProviderName: "console", models: [NEED_MODEL],
  });
  const [admit] = useAuthoredMutation(AdmitNeedRequesterDocument, {
    dataProviderName: "console", invalidateModels: [NEED_MODEL, "projects.Task"],
  });
  const [remove] = useAuthoredMutation(RemoveNeedRequesterDocument, {
    dataProviderName: "console", invalidateModels: [NEED_MODEL, "projects.Task"],
  });
  const settle = useActionResultRun();
  const needs = React.useMemo(() => query.data?.intake_needs ?? [], [query.data?.intake_needs]);
  const vacant = React.useMemo(() => needs.filter((need) => !need.party && holdsPermission(need, "write")), [needs]);
  const canManage = Boolean(record && holdsPermission(record, "share"));
  const people = React.useMemo(() => needs.flatMap((need) =>
    need.requester_user && String(need.access_decision?.verdict ?? "").toUpperCase() === "COMPLETED" ? [{
      subject: `auth/user:${need.requester_user}`, label: need.party?.display_name || need.claimed_name || t("access.requester"),
      roleId: "intake.requester", seatId: need.id,
      roleLabel: t("access.requester"), removable: canManage && holdsPermission(need, "write"),
    }] : []), [canManage, needs, t]);
  const add = React.useCallback(async (subject: string) => {
    const need = vacant.length === 1 ? vacant[0] : undefined;
    if (!need || !subject.startsWith("auth/user:")) return false;
    const result = await settle(async () => extractActionOutcome(await admit({
      need: need.id, user: subject.slice("auth/user:".length),
    }), "admit_need_requester"));
    return result?.ok === true;
  }, [admit, settle, vacant]);
  const removePerson = React.useCallback(async (person: { seatId?: string }) => {
    if (!person.seatId) return;
    await settle(async () => extractActionOutcome(await remove({ need: person.seatId }), "remove_need_requester"));
  }, [remove, settle]);
  const state = React.useMemo<AccessRoleState>(() => ({
    role: { id: "intake.requester", label: t("access.requester"), subjectResource: "iam.User",
      offered: canManage && vacant.length === 1 },
    people, add, remove: removePerson,
  }), [add, canManage, people, removePerson, t, vacant.length]);
  useAccessRole("intake.requester", state);
  return null;
}
