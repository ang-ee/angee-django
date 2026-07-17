import * as React from "react";
import {
  Action,
  Button,
  Column,
  Field,
  Form,
  Group,
  List,
  ListView,
  MutationDialog,
  ResourceList,
  Glyph,
  errorMessage,
  type ListColumn,
  type MutationDialogField,
  type RecordPanelContext,
  type RecordTabDescriptor,
  type StringIdRow,
  useResourceRecordHref,
  useAuthoredResourceMutation,
  useConfirm,
  useToast,
} from "@angee/ui";

import {
  AddSpaceMembership,
  RemoveSpaceMembership,
  SPACE_MEMBERSHIP_INVALIDATES,
  UpdateSpaceMembershipRole,
} from "./documents";
import { useSpacesT } from "./i18n";

const MODEL = "spaces.Group";

type MembershipRow = StringIdRow;
type SpaceThreadRow = StringIdRow;

/** Narrow a dialog value onto the wire's MembershipRole enum, defaulting MEMBER. */
function membershipRole(value: unknown): "OWNER" | "MODERATOR" | "MEMBER" {
  return value === "OWNER" || value === "MODERATOR" ? value : "MEMBER";
}

function threadColumns(
  t: ReturnType<typeof useSpacesT>,
): readonly ListColumn<SpaceThreadRow>[] {
  return [
    { field: "title.text", header: t("group.threads.title") },
    { field: "message_count", header: t("group.threads.messages") },
    { field: "last_message_at" },
  ];
}

function GroupRosterTab({ recordId, ...context }: RecordPanelContext): React.ReactElement {
  const t = useSpacesT();
  const confirm = useConfirm();
  const toast = useToast();
  const [addOpen, setAddOpen] = React.useState(false);
  const [roleRow, setRoleRow] = React.useState<MembershipRow | null>(null);
  const [add, addState] = useAuthoredResourceMutation(AddSpaceMembership, {
    invalidateModels: SPACE_MEMBERSHIP_INVALIDATES,
  });
  const [updateRole, updateState] = useAuthoredResourceMutation(
    UpdateSpaceMembershipRole,
    { invalidateModels: SPACE_MEMBERSHIP_INVALIDATES },
  );
  const [remove, removeState] = useAuthoredResourceMutation(RemoveSpaceMembership, {
    invalidateModels: SPACE_MEMBERSHIP_INVALIDATES,
  });
  const busy =
    addState.fetching ||
    updateState.fetching ||
    removeState.fetching;
  const roleOptions = React.useMemo(
    () => [
      { value: "OWNER", label: t("group.roster.role.owner") },
      { value: "MODERATOR", label: t("group.roster.role.moderator") },
      { value: "MEMBER", label: t("group.roster.role.member") },
    ],
    [t],
  );
  const addFields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "party",
        label: t("group.roster.party"),
        required: true,
        relation: { resource: "parties.Party", labelField: "display_name" },
      },
      {
        name: "role",
        label: t("group.roster.role"),
        widget: "select",
        options: roleOptions,
        required: true,
      },
    ],
    [roleOptions, t],
  );
  const roleFields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "role",
        label: t("group.roster.role"),
        widget: "select",
        options: roleOptions,
        required: true,
      },
    ],
    [roleOptions, t],
  );
  const columns = React.useMemo<readonly ListColumn<MembershipRow>[]>(
    () => [
      { field: "party.display_name", header: t("group.roster.party") },
      { field: "role", header: t("group.roster.role") },
      { field: "is_confirmed" },
      { field: "source" },
      { field: "created_at" },
      {
        field: "id",
        header: t("group.roster.actions"),
        headerVisuallyHidden: true,
        sortable: false,
        align: "right",
        render: (row) => (
          <span className="inline-flex gap-1">
            <Button
              type="button"
              variant="ghost"
              size="iconSm"
              aria-label={t("group.roster.changeRole")}
              title={t("group.roster.changeRole")}
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation();
                setRoleRow(row);
              }}
            >
              <Glyph decorative name="pencil" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="iconSm"
              aria-label={t("group.roster.remove")}
              title={t("group.roster.remove")}
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation();
                void removeMember(row);
              }}
            >
              <Glyph decorative name="trash" />
            </Button>
          </span>
        ),
      },
    ],
    [busy, t],
  );
  void context;

  async function removeMember(row: MembershipRow): Promise<void> {
    const accepted = await confirm({
      title: t("group.roster.removeTitle"),
      body: t("group.roster.removeDescription"),
      confirm: t("group.roster.remove"),
      danger: true,
    });
    if (!accepted) return;
    try {
      await remove({ id: row.id });
    } catch (cause) {
      toast.danger({
        title: t("group.roster.removeError"),
        description: errorMessage(cause, t("group.roster.removeError")),
      });
    }
  }

  return (
    <>
      <ListView<MembershipRow>
        resource="spaces.Membership"
        scope="local"
        fields={["id", "party.display_name", "role", "is_confirmed", "source", "created_at"]}
        baseFilter={{ group: { exact: recordId } }}
        columns={columns}
        toolbarActions={
          <Button type="button" variant="primary" size="sm" onClick={() => setAddOpen(true)}>
            <Glyph decorative name="plus" />
            {t("group.roster.add")}
          </Button>
        }
        emptyContent={t("group.roster.empty")}
      />
      <MutationDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        title={t("group.roster.add")}
        fields={addFields}
        initialValues={{ role: "MEMBER" }}
        submitLabel={t("group.roster.add")}
        submittingLabel={t("group.roster.adding")}
        errorFallback={t("group.roster.addError")}
        onSubmit={(values) =>
          add({
            group: recordId,
            party: String(values.party ?? ""),
            role: membershipRole(values.role),
          })
        }
      />
      <MutationDialog
        open={roleRow !== null}
        onOpenChange={(open) => {
          if (!open) setRoleRow(null);
        }}
        title={t("group.roster.changeRole")}
        fields={roleFields}
        initialValues={{ role: String(roleRow?.role ?? "MEMBER") }}
        submitLabel={t("group.roster.saveRole")}
        submittingLabel={t("group.roster.savingRole")}
        errorFallback={t("group.roster.roleError")}
        onSubmit={(values) =>
          updateRole({
            id: roleRow?.id ?? "",
            role: String(values.role ?? "MEMBER"),
          })
        }
        onSubmitted={() => setRoleRow(null)}
      />
    </>
  );
}

function GroupThreadsTab({ recordId, ...context }: RecordPanelContext): React.ReactElement {
  const t = useSpacesT();
  const threadHref = useResourceRecordHref("messaging.Thread");
  void context;
  return (
    <ListView<SpaceThreadRow>
      resource="spaces.GroupThread"
      scope="local"
      fields={["id", "title.text", "message_count", "last_message_at"]}
      baseFilter={{ group: { exact: recordId } }}
      columns={threadColumns(t)}
      rowHref={threadHref === undefined ? undefined : (thread) => threadHref(thread.id)}
      emptyContent={t("group.threads.empty")}
    />
  );
}

function groupRecordTabs(t: ReturnType<typeof useSpacesT>): readonly RecordTabDescriptor[] {
  return [
    {
      id: "roster",
      label: t("group.tabs.roster"),
      render: (context) => <GroupRosterTab {...context} />,
    },
    {
      id: "threads",
      label: t("group.tabs.threads"),
      render: (context) => <GroupThreadsTab {...context} />,
    },
  ];
}

/** Shared spaces compose the common resource list, roster list, and messaging thread detail. */
export function SpacesPage(): React.ReactElement {
  const t = useSpacesT();
  const tabs = React.useMemo(() => groupRecordTabs(t), [t]);
  return (
    <ResourceList resource={MODEL} placement="inline" routed recordTabs={tabs}>
      <List resource={MODEL}>
        <Column field="name" />
        <Column field="parent.name" header={t("group.parent")} />
        <Column field="visibility" header={t("group.visibility")} />
        <Column field="created_at" />
      </List>
      <Form resource={MODEL}>
        <Field name="name" title />
        <Group label={t("group.details")} columns={2}>
          <Field name="slug" />
          <Field name="parent" label={t("group.parent")} />
          <Field name="visibility" label={t("group.visibility")} readOnly />
        </Group>
        <Field name="description" />
        <Action
          id="visibility-public"
          label={t("group.makePublic")}
          set={{ visibility: "public" }}
          visibleWhen={(record) => record.visibility !== "PUBLIC"}
        />
        <Action
          id="visibility-private"
          label={t("group.makePrivate")}
          set={{ visibility: "private" }}
          visibleWhen={(record) => record.visibility !== "PRIVATE"}
        />
      </Form>
    </ResourceList>
  );
}
