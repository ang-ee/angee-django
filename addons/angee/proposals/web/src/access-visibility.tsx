import * as React from "react";
import { holdsPermission } from "@angee/metadata";
import { useAccessVisibility, type AccessRoleOwnerProps } from "@angee/iam";
import { extractActionOutcome, useAuthoredMutation } from "@angee/refine";
import { useActionResultRun, useConfirm } from "@angee/ui";

import { OPEN_ROUND } from "./documents";
import { useProposalsT } from "./i18n";
import { openingPolicyLabelKey, openingPolicyMessageKey } from "./round-actions";
import { ROUND_MODEL } from "./resources";

/** The round's selected audience and irreversible opening verb. */
export function RoundOpeningAccessVisibility({ targetId, record }: AccessRoleOwnerProps): null {
  const t = useProposalsT();
  const confirm = useConfirm();
  const settle = useActionResultRun();
  const [open] = useAuthoredMutation(OPEN_ROUND, {
    dataProviderName: "console", invalidateModels: [ROUND_MODEL],
  });
  const policy = typeof record?.opening_policy === "string" ? record.opening_policy : "";
  const offered = record?.can_open === true && holdsPermission(record, "manage");
  const onAct = React.useCallback(async () => {
    if (!await confirm({
      title: t("round.action.openTitle"), body: t(openingPolicyMessageKey(policy)),
      confirm: t("round.action.open"),
    })) return;
    await settle(async () => extractActionOutcome(await open({
      round: targetId, revision: typeof record?.revision === "number" ? record.revision : undefined,
    }), "open_proposal_round"));
  }, [confirm, open, policy, record?.revision, settle, t, targetId]);
  const state = React.useMemo(() => ({
    id: "proposals.opening", label: t("round.visibility.opening"),
    value: policy, options: policy ? [{ value: policy, label: t(openingPolicyLabelKey(policy)) }] : [],
    consequence: t(openingPolicyMessageKey(policy)),
    ...(offered ? { actionLabel: t("round.action.open"), onAct } : {}),
  }), [offered, onAct, policy, t]);
  useAccessVisibility("proposals.opening", state);
  return null;
}
