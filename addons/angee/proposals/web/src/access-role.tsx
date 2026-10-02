import * as React from "react";
import { holdsPermission, rowValueAtPath } from "@angee/metadata";
import { extractActionOutcome, useAuthoredMutation } from "@angee/refine";
import { useAccessRole, type AccessRoleOwnerProps, type AccessRoleState } from "@angee/iam";
import { useActionResultRun } from "@angee/ui";

import { ADMIT_RESPONDER, REMOVE_RESPONDER } from "./documents";
import { useProposalsT } from "./i18n";
import { ROUND_MODEL } from "./resources";

function userId(subject: string, invalidSubject: string): string {
  if (!subject.startsWith("auth/user:")) throw new Error(invalidSubject);
  return subject.slice("auth/user:".length);
}

/** The round owns both its live roster and its admission/removal verbs. */
export function RoundResponderAccessRole({ targetId, record }: AccessRoleOwnerProps): null {
  const t = useProposalsT();
  const [admit] = useAuthoredMutation(ADMIT_RESPONDER, { dataProviderName: "console", invalidateModels: [ROUND_MODEL] });
  const [remove] = useAuthoredMutation(REMOVE_RESPONDER, { dataProviderName: "console", invalidateModels: [ROUND_MODEL] });
  const settle = useActionResultRun();
  const roster = rowValueAtPath(record ?? {}, "roster");
  const people = React.useMemo(() => Array.isArray(roster) ? roster.flatMap((row: unknown) => {
    if (!row || typeof row !== "object") return [];
    const item = row as Record<string, unknown>;
    if (typeof item.user !== "string" || typeof item.name !== "string") return [];
    return [{
      subject: `auth/user:${item.user}`, label: item.name, roleId: "proposals.responder",
      roleLabel: t("round.action.responder"), removable: Boolean(record && holdsPermission(record, "manage")),
    }];
  }) : [], [record, roster, t]);
  const add = React.useCallback(async (subject: string) => {
    const result = await settle(async () => extractActionOutcome(
      await admit({ round: targetId, responder: userId(subject, t("round.action.invalidResponder")), track: false, follow: true }),
      "admit_proposal_round_responder",
    ));
    return result?.ok === true;
  }, [admit, settle, t, targetId]);
  const removePerson = React.useCallback(async (person: { subject: string }) => {
    const revision = record && typeof record.revision === "number" ? record.revision : undefined;
    await settle(async () => extractActionOutcome(
      await remove({ round: targetId, responder: userId(person.subject, t("round.action.invalidResponder")), revision }),
      "remove_proposal_round_responder",
    ));
  }, [record, remove, settle, t, targetId]);
  const state = React.useMemo<AccessRoleState>(() => ({
    role: {
      id: "proposals.responder", label: t("round.action.responder"), subjectResource: "iam.User",
      offered: record?.can_admit === true,
    },
    people, add, remove: removePerson,
  }), [add, people, record?.can_admit, removePerson, t]);
  useAccessRole("proposals.responder", state);
  return null;
}
