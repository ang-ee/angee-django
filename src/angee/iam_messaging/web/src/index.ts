import { defineBaseAddon } from "@angee/app";
import { ACCOUNT_ACTION_FIELDS } from "@angee/iam";
import { lazyRouteComponent } from "@tanstack/react-router";
import { createElement } from "react";

import { IssueSignInLinkRecordAction } from "./account-actions";
import { enIamMessagingMessages } from "./i18n";

// The public route a sign-in link opens; the server builds links to this same
// path (`SIGN_IN_LINK_PATH` in `angee.iam_messaging.models`).
const SIGN_IN_LINK_PATH = "/sign-in-link";

const iamMessaging = defineBaseAddon({
  id: "iam_messaging",
  routes: [
    // The page a sign-in link opens, outside the console chrome.
    {
      name: "iam_messaging.sign-in-link",
      path: SIGN_IN_LINK_PATH,
      layout: "public",
      component: lazyRouteComponent(() => import("./SignInLinkPage"), "SignInLinkPage"),
    },
  ],
  i18n: { iam_messaging: enIamMessagingMessages },
  containers: {
    // IAM's account actions on every saved user form, this one offered by the row's `account_actions`.
    "iam.User#actions-menu": {
      "iam_messaging.issue-sign-in-link": {
        sequence: 9,
        requiredFields: ACCOUNT_ACTION_FIELDS,
        content: createElement(IssueSignInLinkRecordAction),
      },
    },
  },
});

export default iamMessaging;
