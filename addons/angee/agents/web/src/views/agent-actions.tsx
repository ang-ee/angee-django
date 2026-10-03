import * as React from "react";
import type { Row } from "@angee/metadata";
import { Action, useRecordActionMutation, type ActionConfirm } from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useAgentsT } from "../i18n";
import { agentInstanceKind, booleanField, stringField } from "./agent-record";

export const AGENT_MODEL = "agents.Agent";

/** The agent fields its lifecycle actions read: verb eligibility and the conflicting instance. */
export const AGENT_LIFECYCLE_FIELDS = [
  "can_provision",
  "can_adopt",
  "can_replace",
  "can_reprovision",
  "can_deprovision",
  "can_delete",
  "conflict_kind",
  "conflict_name",
] as const;

type AgentsT = ReturnType<typeof useAgentsT>;

/** The verb's own eligibility, projected by the backend onto the record. */
function eligible(field: (typeof AGENT_LIFECYCLE_FIELDS)[number]): (record: Row) => boolean {
  return (record) => booleanField(record, field);
}

function conflictConfirm(t: AgentsT, verb: "adopt" | "replace", record: Row): ActionConfirm {
  const kind = agentInstanceKind(record);
  const name = stringField(record, "conflict_name");
  return {
    title: kind ? t(`provisioning.${verb}Title.${kind}`) : t(`provisioning.${verb}`),
    ...(kind ? { body: t(`provisioning.${verb}Body.${kind}`, { name }) } : {}),
    ...(verb === "replace" ? { danger: true } : {}),
  };
}

function deprovisionConfirm(t: AgentsT, record: Row): ActionConfirm {
  const name = stringField(record, "conflict_name");
  const kind = agentInstanceKind(record);
  return {
    title: t("provisioning.deprovisionTitle"),
    body: name && kind
      ? t(`provisioning.deprovisionBody.${kind}`, { name })
      : t("provisioning.deprovisionBody"),
    danger: true,
  };
}

/**
 * The agent's lifecycle verbs as declared record actions. Each is shown by the
 * backend's own eligibility for that verb, so a recorded conflicting instance
 * swaps Provision for Adopt existing / Replace existing.
 */
export function useAgentLifecycleActions(): React.ReactElement[] {
  const t = useAgentsT();
  // These verbs record their outcome even when they fail (the error, a conflicting
  // instance), so the record refreshes after a failure as well as after success.
  const options = {
    invalidateModels: [AGENT_MODEL],
    invalidateOnFailure: true,
    missingRecordMessage: t("provisioning.saveFirst"),
  };
  const [provision] = useRecordActionMutation<ActionFieldName>("provision_agent", options);
  const [adopt] = useRecordActionMutation<ActionFieldName>("adopt_agent", options);
  const [replace] = useRecordActionMutation<ActionFieldName>("replace_agent", options);
  const [reprovision] = useRecordActionMutation<ActionFieldName>("reprovision_agent", options);
  const [deprovision] = useRecordActionMutation<ActionFieldName>("deprovision_agent", options);
  return [
    <Action
      key="provision"
      id="provision"
      label={t("provisioning.provision")}
      icon="plus"
      placement="toolbar"
      primary
      visibleWhen={eligible("can_provision")}
      run={provision}
    />,
    <Action
      key="adopt"
      id="adopt"
      label={t("provisioning.adopt")}
      icon="link"
      placement="toolbar"
      primary
      confirm={(record) => conflictConfirm(t, "adopt", record)}
      visibleWhen={eligible("can_adopt")}
      run={adopt}
    />,
    <Action
      key="replace"
      id="replace"
      label={t("provisioning.replace")}
      icon="refresh"
      placement="toolbar"
      danger
      confirm={(record) => conflictConfirm(t, "replace", record)}
      visibleWhen={eligible("can_replace")}
      run={replace}
    />,
    <Action
      key="reprovision"
      id="reprovision"
      label={t("provisioning.reprovision")}
      icon="refresh"
      confirm={{ title: t("provisioning.reprovisionTitle"), body: t("provisioning.reprovisionBody") }}
      visibleWhen={eligible("can_reprovision")}
      run={reprovision}
    />,
    <Action
      key="deprovision"
      id="deprovision"
      label={t("provisioning.deprovision")}
      icon="trash"
      danger
      confirm={(record) => deprovisionConfirm(t, record)}
      visibleWhen={eligible("can_deprovision")}
      run={deprovision}
    />,
  ];
}
