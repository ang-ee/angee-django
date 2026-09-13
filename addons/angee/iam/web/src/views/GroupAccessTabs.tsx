import { useMemo, type ReactElement } from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Button, Code, MutationDialog, RowsListView, SubjectPicker, TextLink,
  defineRowAction, mutationDialogValueCodecs, useAuthoredResourceMutation,
  useResourceRecordHref, useResourceRoute,
  type ListColumn, type MutationDialogField, type RecordPanelContext,
} from "@angee/ui";

import {
  IAM_GROUP_MUTATION_INVALIDATES, IamAddGroupMember, IamGroupAccess, IamRemoveGroupMember,
  type IAMGroupBinding, type IAMGroupMember,
} from "../documents";
import { useIamT } from "../i18n";

export function GroupMembersTab({ recordId }: RecordPanelContext): ReactElement {
  const t = useIamT();
  const query = useAuthoredQuery(IamGroupAccess, { id: recordId }, { models: IAM_GROUP_MUTATION_INVALIDATES });
  const [addMember] = useAuthoredResourceMutation(IamAddGroupMember, { invalidateModels: IAM_GROUP_MUTATION_INVALIDATES });
  const members = useMemo(() => query.data?.groups_by_pk?.members ?? [], [query.data]);
  const fields = useMemo<readonly MutationDialogField[]>(() => [{
    name: "subject",
    label: t("group.member"),
    required: true,
    control: ({ id, value, readOnly, describedBy, labelledBy, onChange }) => <SubjectPicker
      id={id}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      resource="iam.User"
      value={typeof value === "string" ? value : ""}
      readOnly={readOnly}
      onChange={onChange}
    />,
  }], [t]);
  const columns = useMemo<readonly ListColumn<IAMGroupMember>[]>(() => [
    { field: "label", header: t("group.member") },
    { field: "subject", header: t("group.subject"), render: (row) => <Code truncate>{row.subject}</Code> },
    { field: "caveat_name", header: t("group.caveat") },
  ], [t]);
  const actions = useMemo(() => [defineRowAction({
    kind: "authored",
    id: "remove-group-member",
    label: t("group.remove"),
    document: IamRemoveGroupMember,
    variables: (row: IAMGroupMember) => ({ group_id: recordId, subject: row.subject, caveat_name: row.caveat_name }),
    succeeded: (result) => result?.remove_group_member === true,
    invalidateModels: IAM_GROUP_MUTATION_INVALIDATES,
    confirm: {
      title: () => t("group.removeTitle"),
      body: (row: IAMGroupMember) => t("group.removeDescription", { name: row.label }),
      confirm: () => t("group.remove"),
    },
    toast: { title: () => t("group.removeError"), description: () => t("group.removeError") },
    variant: "danger",
    pendingPolicy: "active-row",
  })], [recordId, t]);
  return <RowsListView
    rows={members}
    columns={columns}
    rowActions={actions}
    fetching={query.isFetching}
    error={query.error}
    selectable={false}
    scope="local"
    emptyContent={t("group.noMembers")}
    toolbarActions={<MutationDialog
      trigger={<Button type="button" variant="primary" size="sm">{t("group.add")}</Button>}
      title={t("group.add")}
      description={t("group.membersDescription")}
      fields={fields}
      submitLabel={t("group.add")}
      errorFallback={t("group.addError")}
      parseValues={(values) => ({ subject: mutationDialogValueCodecs.requiredString(values.subject, "subject") })}
      onSubmit={async ({ subject }) => {
        const result = await addMember({ group_id: recordId, subject, caveat_name: "" });
        if (!result?.add_group_member) throw new Error(t("group.addError"));
      }}
    />}
  />;
}

function BindingTarget({ binding }: { binding: IAMGroupBinding }): ReactElement {
  if (!binding.target_model) return <Code truncate>{binding.resource}</Code>;
  return <RoutedBindingTarget binding={binding} targetModel={binding.target_model} />;
}

function RoutedBindingTarget({ binding, targetModel }: {
  binding: IAMGroupBinding;
  targetModel: string;
}): ReactElement {
  const recordHref = useResourceRecordHref(targetModel);
  const collectionHref = useResourceRoute(targetModel);
  const href = (binding.target_id ? recordHref?.(binding.target_id) : undefined) ?? collectionHref;
  return href
    ? <TextLink href={href}>{binding.resource}</TextLink>
    : <Code truncate>{binding.resource}</Code>;
}

export function GroupBindingsTab({ recordId }: RecordPanelContext): ReactElement {
  const t = useIamT();
  const query = useAuthoredQuery(IamGroupAccess, { id: recordId }, { models: IAM_GROUP_MUTATION_INVALIDATES });
  const bindings = useMemo(() => query.data?.groups_by_pk?.bindings ?? [], [query.data]);
  const columns = useMemo<readonly ListColumn<IAMGroupBinding>[]>(() => [
    { field: "resource", header: t("group.resource"), render: (row) => <BindingTarget binding={row} /> },
    { field: "relation", header: t("group.relation") },
    { field: "caveat_name", header: t("group.caveat") },
  ], [t]);
  return <RowsListView
    rows={bindings}
    columns={columns}
    fetching={query.isFetching}
    error={query.error}
    selectable={false}
    scope="local"
    emptyContent={t("group.noBindings")}
  />;
}
