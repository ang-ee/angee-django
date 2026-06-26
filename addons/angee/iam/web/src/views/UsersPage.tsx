import * as React from "react";
import {
  Action,
  Column,
  ResourceList,
  Field,
  Form,
  Group,
  List,
} from "@angee/ui";

import { useIamT } from "../i18n";

const MODEL = "User";

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
  const userForm = (
    <Form resource={MODEL}>
      <Field name="username" title />
      <Group label={t("iam.users.group.profile")} columns={2}>
        <Field name="email" />
        <Field name="first_name" />
        <Field name="last_name" />
      </Group>
      <Group label={t("iam.users.group.access")} columns={2}>
        <Field name="is_staff" />
        <Field name="is_active" />
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
        set={{ is_active: false }}
        visibleWhen={(record) => record.is_active === true}
      />
      <Action
        id="activate"
        label={t("iam.users.activate")}
        set={{ is_active: true }}
        visibleWhen={(record) => record.is_active === false}
      />
    </Form>
  );
  return (
    <ResourceList resource={MODEL} placement="inline" routed>
      {userList}
      {userForm}
    </ResourceList>
  );
}
