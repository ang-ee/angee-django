import * as React from "react";
import { Action, Column, DataPage, Field, Form, Group, List } from "@angee/base";

import { useIamT } from "../i18n";

const MODEL = "User";

const userList = (
  <List model={MODEL}>
    <Column field="username" />
    <Column field="email" />
    <Column field="isStaff" />
    <Column field="isActive" />
  </List>
);

/** Users (full CRUD; password is write-only and hashed server-side). */
export function UsersPage(): React.ReactElement {
  const t = useIamT();
  const userForm = (
    <Form model={MODEL}>
      <Field name="username" title />
      <Group label={t("iam.users.group.profile")} columns={2}>
        <Field name="email" />
        <Field name="firstName" />
        <Field name="lastName" />
      </Group>
      <Group label={t("iam.users.group.access")} columns={2}>
        <Field name="isStaff" />
        <Field name="isActive" />
      </Group>
      {/* Write-only: set on create, hashed server-side; password reset is separate. */}
      <Field name="password" widget="text" kind="string" createOnly />
      {/* Reset password collects a value and patches it through update (hashed server-side). */}
      <Action
        id="reset-password"
        label={t("iam.users.resetPassword")}
        prompt={{
          title: t("iam.users.resetPassword.title"),
          body: t("iam.users.resetPassword.body"),
          fields: [
            {
              name: "password",
              label: t("iam.users.resetPassword.fieldLabel"),
              type: "password",
            },
          ],
        }}
      />
      <Action
        id="deactivate"
        label={t("iam.users.deactivate")}
        danger
        set={{ isActive: false }}
        visibleWhen={(record) => record.isActive === true}
      />
      <Action
        id="activate"
        label={t("iam.users.activate")}
        set={{ isActive: true }}
        visibleWhen={(record) => record.isActive === false}
      />
    </Form>
  );
  return (
    <DataPage model={MODEL} placement="inline" routed>
      {userList}
      {userForm}
    </DataPage>
  );
}
