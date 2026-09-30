import { useMemo } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { Code, RowsListView, defineRowAction, type ListColumn } from "@angee/ui";

import { RevokeWorkflowTriggerGrantDocument, TriggerGrantsDocument } from "./documents.console";
import { useWorkflowsT } from "./i18n";
import { TRIGGER_MODEL } from "./triggers";

type GrantRow = {
  id: string;
  resource_type: string;
  resource_id: string;
  relation: string;
  target_label: string | null;
};

/** Direct principal grants on the native trigger record tab. */
export function TriggerGrantsTab({ recordId }: { recordId: string }) {
  const t = useWorkflowsT();
  const query = useAuthoredQuery(TriggerGrantsDocument, { id: recordId }, { models: [TRIGGER_MODEL] });
  const trigger = query.data?.trigger_by_pk;
  const rows = useMemo<GrantRow[]>(() => (trigger?.grants ?? []).map((grant) => ({
    ...grant, id: `${grant.resource_type}:${grant.resource_id}#${grant.relation}`,
  })), [trigger?.grants]);
  const columns = useMemo<readonly ListColumn<GrantRow>[]>(() => [
    { field: "target_label", header: t("trigger.grantTarget"), render: (row) => row.target_label ?? `${row.resource_type}:${row.resource_id}` },
    { field: "resource_type", header: t("trigger.grantResource"), render: (row) => <Code truncate>{row.resource_type}:{row.resource_id}</Code> },
    { field: "relation", header: t("trigger.grantRelation") },
  ], [t]);
  const actions = useMemo(() => [defineRowAction({
    kind: "authored",
    id: "revoke-trigger-grant",
    label: t("trigger.revokeGrant"),
    document: RevokeWorkflowTriggerGrantDocument,
    variables: (row: GrantRow) => ({
      id: recordId, resourceType: row.resource_type, resourceId: row.resource_id, relation: row.relation,
    }),
    visible: () => trigger?.can_edit === true,
    succeeded: (result) => result?.revoke_workflow_trigger_grant?.ok === true,
    invalidateModels: [TRIGGER_MODEL],
    confirm: {
      title: () => t("trigger.revokeGrant"),
      body: () => t("trigger.revokeGrantDescription"),
      confirm: () => t("trigger.revokeGrant"),
    },
    toast: { title: () => t("trigger.revokeGrantError"), description: () => t("trigger.revokeGrantError") },
    variant: "danger",
    pendingPolicy: "active-row",
  })], [recordId, t, trigger?.can_edit]);

  return <RowsListView<GrantRow>
    rows={rows} columns={columns} rowActions={actions} fetching={query.isFetching} error={query.error}
    selectable={false} scope="local" emptyContent={t("trigger.noGrants")}
  />;
}
