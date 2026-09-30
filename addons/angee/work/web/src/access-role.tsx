import * as React from "react";
import { extractActionOutcome, useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import { useAccessRole, type AccessRoleOwnerProps, type AccessRoleState } from "@angee/iam";
import { useActionResultRun } from "@angee/ui";

import { AdmitProjectManagerDocument, ProjectManagerRosterDocument, RemoveProjectManagerDocument } from "./documents";
import { useWorkT } from "./i18n";
import { PROJECT_MODEL } from "@angee/projects";

/** The project's team roster owns its manager seats. */
export function ProjectManagerAccessRole({ targetId }: AccessRoleOwnerProps): null {
  const t = useWorkT();
  const query = useAuthoredQuery(ProjectManagerRosterDocument, { project: targetId }, {
    dataProviderName: "console", models: [PROJECT_MODEL, "spaces.Membership"],
  });
  const [admit] = useAuthoredMutation(AdmitProjectManagerDocument, {
    dataProviderName: "console", invalidateModels: [PROJECT_MODEL, "spaces.Membership"],
  });
  const [remove] = useAuthoredMutation(RemoveProjectManagerDocument, {
    dataProviderName: "console", invalidateModels: [PROJECT_MODEL, "spaces.Membership"],
  });
  const settle = useActionResultRun();
  const roster = query.data?.project_manager_roster;
  const people = React.useMemo(() => roster?.people.map((person) => ({
    subject: person.subject, label: person.label, seatId: person.seat_id,
    roleId: "work.manager", roleLabel: t("access.manager"), removable: person.removable,
  })) ?? [], [roster?.people, t]);
  const add = React.useCallback(async (subject: string) => {
    if (!subject.startsWith("auth/user:")) return false;
    const result = await settle(async () => extractActionOutcome(await admit({
      project: targetId, user: subject.slice("auth/user:".length),
    }), "admit_project_manager"));
    return result?.ok === true;
  }, [admit, settle, targetId]);
  const removePerson = React.useCallback(async (person: { seatId?: string }) => {
    if (!person.seatId) return;
    await settle(async () => extractActionOutcome(await remove({
      project: targetId, seat: person.seatId,
    }), "remove_project_manager"));
  }, [remove, settle, targetId]);
  const state = React.useMemo<AccessRoleState>(() => ({
    role: { id: "work.manager", label: t("access.manager"), subjectResource: "iam.User",
      offered: roster?.offered === true },
    people, add, remove: removePerson,
  }), [add, people, removePerson, roster?.offered, t]);
  useAccessRole("work.manager", state);
  return null;
}
