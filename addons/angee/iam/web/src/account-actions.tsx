import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import type { Row } from "@angee/metadata";
import { useAuthoredMutation } from "@angee/refine";
import {
  RecordActionBar,
  useActionOutcomeMutation,
  useActionResultMutation,
  usePrompt,
  useRecordAction,
  useRecordChromeContext,
  type ActionArg,
  type ActionDescriptor,
} from "@angee/ui";

import { IamIssueUserPassword, IamResetUserPassword } from "./documents";
import { useIamT } from "./i18n";

const USER_MODEL = "iam.User";

/** Account verbs the server projects per user row, spelled as their Zed permissions. */
export type UserAccountAction = "set_active" | "rename" | "reset_password" | "issue_password";

/** Fields every account verb reads from the open user record. */
export const ACCOUNT_ACTION_FIELDS = ["account_actions", "revision", "username"] as const;

/** Whether the row's server projection offers `action`; the client never derives eligibility itself. */
export function offersAccountAction(record: Row, action: UserAccountAction): boolean {
  const offered = record.account_actions;
  return Array.isArray(offered) && offered.some((value) => String(value).toLowerCase() === action);
}

/** The revision the open record was read at, sent so a stale account write is refused. */
function expectedRevision(record: Row | null): number {
  return Number(record?.revision);
}

interface SignInDetails {
  username: string;
  password: string;
}

/** Reveal one-time sign-in details only through the read-only prompt; nothing reaches a cache. */
function useSignInReveal(): (title: string, details: SignInDetails | null | undefined) => Promise<void> {
  const t = useIamT();
  const prompt = usePrompt();
  return React.useCallback(async (title: string, details: SignInDetails | null | undefined) => {
    if (!details?.password) throw new Error(t("users.access.noPassword"));
    await prompt({
      title,
      body: t("users.access.revealBody"),
      fields: [
        { name: "username", label: t("users.access.username"), defaultValue: details.username,
          readOnly: true, copyable: true },
        { name: "password", label: t("users.access.password"), defaultValue: details.password,
          readOnly: true, copyable: true },
      ],
      copy: {
        label: t("users.access.copySignIn"),
        value: t("users.access.signInDetails", { username: details.username, password: details.password }),
      },
    });
  }, [prompt, t]);
}

/** Give a passwordless person a first password, revealed once with their username. */
export function useIssuePasswordAction(): ActionDescriptor {
  const t = useIamT();
  const reveal = useSignInReveal();
  const [issuePassword] = useAuthoredMutation(IamIssueUserPassword, { transient: true, invalidateModels: [USER_MODEL] });
  const run = useRecordAction(async (id) => {
    const result = await issuePassword({ id });
    await reveal(t("users.giveAccess.title"), result?.issue_user_password);
  });
  return {
    id: "give-access", label: t("users.giveAccess"), run,
    confirm: { title: t("users.giveAccess.confirmTitle"), body: t("users.giveAccess.confirmBody") },
    visibleWhen: (record) => offersAccountAction(record, "issue_password"),
  };
}

/** Replace a person's password with a new one-time secret, revealed once with their username. */
export function useResetPasswordAction(): ActionDescriptor {
  const t = useIamT();
  const reveal = useSignInReveal();
  const [resetPassword] = useAuthoredMutation(IamResetUserPassword, { transient: true, invalidateModels: [USER_MODEL] });
  const run = useRecordAction(async (id, context) => {
    const result = await resetPassword({ id, confirmed: true, expectedRevision: expectedRevision(context.record) });
    await reveal(t("users.resetAccess.title"), result?.reset_user_password);
  });
  return {
    id: "reset-access", label: t("users.resetAccess"), run, danger: true,
    confirm: { title: t("users.resetAccess.confirmTitle"), body: t("users.resetAccess.confirmBody"), danger: true },
    visibleWhen: (record) => offersAccountAction(record, "reset_password"),
  };
}

/** Deactivate and reactivate, each confirmed and offered by the row's server projection. */
export function useSetActiveActions(): readonly ActionDescriptor[] {
  const t = useIamT();
  const [setActive] = useActionResultMutation<ActionFieldName>("set_user_active", { invalidateModels: [USER_MODEL] });
  const deactivate = useRecordAction((id, context) =>
    setActive(id, { active: false, confirmed: true, expected_revision: expectedRevision(context.record) }));
  const reactivate = useRecordAction((id, context) =>
    setActive(id, { active: true, confirmed: true, expected_revision: expectedRevision(context.record) }));
  return [
    {
      id: "deactivate", label: t("users.deactivate"), danger: true, run: deactivate,
      confirm: { title: t("users.deactivate.confirmTitle"), body: t("users.deactivate.confirmBody"), danger: true },
      visibleWhen: (record: Row) => record.is_active === true && offersAccountAction(record, "set_active"),
    },
    {
      id: "reactivate", label: t("users.reactivate"), run: reactivate,
      confirm: { title: t("users.reactivate.confirmTitle"), body: t("users.reactivate.confirmBody") },
      visibleWhen: (record: Row) => record.is_active === false && offersAccountAction(record, "set_active"),
    },
  ];
}

/** Rename a person: name fields only, never the username or email. */
export function useRenameAction(): ActionDescriptor {
  const t = useIamT();
  const [rename] = useActionOutcomeMutation<ActionFieldName>("rename_user", { invalidateModels: [USER_MODEL] });
  const args = React.useMemo<readonly ActionArg[]>(() => [
    { name: "first_name", label: t("users.rename.firstName"), optional: true,
      fromContext: ({ record }) => record?.first_name ?? "" },
    { name: "last_name", label: t("users.rename.lastName"), optional: true,
      fromContext: ({ record }) => record?.last_name ?? "" },
  ], [t]);
  const submit = React.useCallback<NonNullable<ActionDescriptor["submit"]>>(async (values, context) => {
    const id = typeof context.record?.id === "string" ? context.record.id : "";
    const outcome = await rename(id, {
      first_name: typeof values.first_name === "string" ? values.first_name : "",
      last_name: typeof values.last_name === "string" ? values.last_name : "",
      confirmed: true,
      expected_revision: expectedRevision(context.record),
    });
    return outcome ?? { ok: false, message: t("users.rename.error") };
  }, [rename, t]);
  return {
    id: "rename", label: t("users.rename"), args, submit,
    visibleWhen: (record) => offersAccountAction(record, "rename"),
  };
}

function AccountActionBar({ actions }: { actions: readonly ActionDescriptor[] }): React.ReactElement | null {
  const { record } = useRecordChromeContext();
  return record ? <RecordActionBar record={record} actions={actions} /> : null;
}

/** `iam.User#actions-menu` child: deactivate or reactivate the open account. */
export function SetActiveRecordActions(): React.ReactElement | null {
  const actions = useSetActiveActions();
  return <AccountActionBar actions={actions} />;
}

/** `iam.User#actions-menu` child: rename the open account. */
export function RenameRecordAction(): React.ReactElement | null {
  const action = useRenameAction();
  return <AccountActionBar actions={[action]} />;
}

/** `iam.User#actions-menu` child: give the open account its first password. */
export function IssuePasswordRecordAction(): React.ReactElement | null {
  const action = useIssuePasswordAction();
  return <AccountActionBar actions={[action]} />;
}

/** `iam.User#actions-menu` child: reset the open account's password. */
export function ResetPasswordRecordAction(): React.ReactElement | null {
  const action = useResetPasswordAction();
  return <AccountActionBar actions={[action]} />;
}
