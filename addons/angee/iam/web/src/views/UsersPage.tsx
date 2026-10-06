import * as React from "react";
import { ResourceList, Field, Form, Group, List, containerContents, useContainer } from "@angee/ui";

import { useIamT } from "../i18n";
import { usePrincipalAccessRecordTab } from "../PrincipalAccess";
import { USER_DIRECTORY_FILTER, USER_LIST_PRESET_IDS, USER_MODEL } from "../users-list";

/**
 * The managed people list: the viewer's people directory, so it lists every
 * account for people managers and no one for anyone else. Columns come from
 * `iam.users#columns`; account verbs (deactivate, rename, give or reset access)
 * are the `iam.User#actions-menu` children, each offered by the row's
 * server-projected `account_actions`. Administrators also edit identity fields
 * here; password is write-only.
 */
export function UsersPage(): React.ReactElement {
  const t = useIamT();
  const accessTab = usePrincipalAccessRecordTab();
  const columns = useContainer("iam.users#columns");
  return (
    <ResourceList resource={USER_MODEL} placement="inline" routed recordTabs={[accessTab]} baseFilter={USER_DIRECTORY_FILTER}>
      <List resource={USER_MODEL} presetIds={USER_LIST_PRESET_IDS}>
        {containerContents(columns)}
      </List>
      <Form resource={USER_MODEL}>
        <Field name="username" title />
        {/* The update sends the revision it read, so a stale edit is refused. */}
        <Field name="revision" readOnly hidden />
        <Group label={t("users.group.profile")} columns={2}>
          <Field name="email" />
          <Field name="first_name" />
          <Field name="last_name" />
        </Group>
        <Group label={t("users.group.access")} columns={2}>
          <Field name="is_staff" editOnly />
          <Field name="is_active" editOnly />
          <Field name="last_login" editOnly readOnly />
        </Group>
        {/* Write-only: set on create, hashed server-side; resets go through the reset-access verb. */}
        <Field name="password" widget="text" kind="string" createOnly />
      </Form>
    </ResourceList>
  );
}
