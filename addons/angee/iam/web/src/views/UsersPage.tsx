import * as React from "react";
import { useAuthoredMutation } from "@angee/refine";
import { Action, Column, ResourceList, Field, Form, Group, List, useRecordAction, type RecordActionRunner } from "@angee/ui";

import { IamIssueUserPassword } from "../documents";
import { useIamT } from "../i18n";
import { usePrincipalAccessRecordTab } from "../PrincipalAccess";

const MODEL = "iam.User";

const userList = (
  <List resource={MODEL}>
    <Column field="username" />
    <Column field="email" />
    <Column field="is_staff" />
    <Column field="is_active" />
  </List>
);

/** Users (full CRUD; password is write-only and hashed server-side). */
export function UsersPage(): React.ReactElement {
  const t = useIamT();
  const accessTab = usePrincipalAccessRecordTab();
  const [issuePassword] = useAuthoredMutation(IamIssueUserPassword, { transient: true });
  const issuePasswordById = React.useCallback<RecordActionRunner>(
    async (id, context) => {
      const result = await issuePassword({ id });
      const password = result?.issue_user_password.password;
      if (!password) throw new Error(t("users.giveAccess.noPassword"));
      await context.prompt({
        title: t("users.giveAccess.title"),
        body: t("users.giveAccess.body"),
        fields: [
          {
            name: "password",
            label: t("users.giveAccess.fieldLabel"),
            defaultValue: password,
            readOnly: true,
            copyable: true,
          },
        ],
      });
    },
    [issuePassword, t],
  );
  const giveAccess = useRecordAction(issuePasswordById);
  const userForm = (
    <Form resource={MODEL}>
      <Field name="username" title />
      <Group label={t("users.group.profile")} columns={2}>
        <Field name="email" />
        <Field name="first_name" />
        <Field name="last_name" />
      </Group>
      <Group label={t("users.group.access")} columns={2}>
        <Field name="is_staff" editOnly />
        <Field name="is_active" editOnly />
      </Group>
      {/* Write-only: set on create, hashed server-side; password reset is separate. */}
      <Field name="password" widget="text" kind="string" createOnly />
      <Action
        id="give-access"
        label={t("users.giveAccess")}
        run={giveAccess}
        confirm={{
          title: t("users.giveAccess.confirmTitle"),
          body: t("users.giveAccess.confirmBody"),
        }}
        visibleWhen={(record) => record.can_issue_password === true}
      />
      {/* Reset password collects a value and patches it through update (hashed server-side). */}
      <Action
        id="reset-password"
        label={t("users.resetPassword")}
        prompt={{
          title: t("users.resetPassword.title"),
          body: t("users.resetPassword.body"),
          fields: [
            {
              name: "password",
              label: t("users.resetPassword.fieldLabel"),
              type: "password",
            },
          ],
        }}
      />
      <Action
        id="deactivate"
        label={t("users.deactivate")}
        danger
        set={{ is_active: false }}
        visibleWhen={(record) => record.is_active === true}
      />
      <Action
        id="activate"
        label={t("users.activate")}
        set={{ is_active: true }}
        visibleWhen={(record) => record.is_active === false}
      />
    </Form>
  );
  return (
    <ResourceList
      resource={MODEL}
      placement="inline"
      routed
      returning={["assignment_subject", "can_issue_password"]}
      recordTabs={[accessTab]}
    >
      {userList}
      {userForm}
    </ResourceList>
  );
}
