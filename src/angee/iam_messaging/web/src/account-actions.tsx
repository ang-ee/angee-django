import type { ActionFieldName } from "@angee/gql/console/actions";
import { AccountActionBar, offersAccountAction, USER_MODEL } from "@angee/iam";
import { useActionResultMutation, useRecordAction, type ActionDescriptor } from "@angee/ui";
import type { ReactElement } from "react";

import { useIamMessagingT } from "./i18n";

/** The account action that emails a sign-in link, named by its Zed permission on `auth/user`. */
export const ISSUE_SIGN_IN_LINK = "issue_sign_in_link";

/** Email a person a single-use sign-in link; the link goes to their email and is never shown here. */
export function useIssueSignInLinkAction(): ActionDescriptor {
  const t = useIamMessagingT();
  const [issueLink] = useActionResultMutation<ActionFieldName>("issue_user_sign_in_link", { invalidateModels: [USER_MODEL] });
  const run = useRecordAction((id) => issueLink(id));
  return {
    id: "send-sign-in-link", label: t("users.sendSignInLink"), run,
    confirm: { title: t("users.sendSignInLink.confirmTitle"), body: t("users.sendSignInLink.confirmBody") },
    visibleWhen: (record) => offersAccountAction(record, ISSUE_SIGN_IN_LINK),
  };
}

/** `iam.User#actions-menu` child: email the open account a sign-in link. */
export function IssueSignInLinkRecordAction(): ReactElement | null {
  const action = useIssueSignInLinkAction();
  return <AccountActionBar actions={[action]} />;
}
