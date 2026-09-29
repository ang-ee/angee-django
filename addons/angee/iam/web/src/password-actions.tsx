import * as React from "react";
import { useAuthoredMutation } from "@angee/refine";
import { RecordActionBar, usePrompt, useRecordAction, useRecordChromeContext, type ActionDescriptor } from "@angee/ui";

import { IamIssueUserPassword } from "./documents";
import { useIamT } from "./i18n";

/** IAM alone owns issuance; the transient response lives only in the reveal prompt. */
export function useIssuePasswordAction(): ActionDescriptor {
  const t = useIamT();
  const prompt = usePrompt();
  const [issuePassword] = useAuthoredMutation(IamIssueUserPassword, { transient: true, invalidateModels: ["iam.User"] });
  const run = useRecordAction(async (id) => {
    const result = await issuePassword({ id });
    const password = result?.issue_user_password.password;
    if (!password) throw new Error(t("users.giveAccess.noPassword"));
    await prompt({
      title: t("users.giveAccess.title"), body: t("users.giveAccess.body"),
      fields: [{ name: "password", label: t("users.giveAccess.fieldLabel"),
        defaultValue: password, readOnly: true, copyable: true }],
    });
  });
  return {
    id: "give-access", label: t("users.giveAccess"), run,
    confirm: { title: t("users.giveAccess.confirmTitle"), body: t("users.giveAccess.confirmBody") },
    visibleWhen: (record) => record.can_issue_password === true,
  };
}

/** The same issuance verb is inherited by every saved IAM user record. */
export function IssuePasswordRecordAction(): React.ReactElement | null {
  const { record } = useRecordChromeContext();
  const action = useIssuePasswordAction();
  return record ? <RecordActionBar record={record} actions={[action]} /> : null;
}
