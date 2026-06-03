import { AUTH_LOGIN_METHOD_SLOT, type BaseAddon } from "@angee/base";
import { createElement } from "react";

import { OAuthCallbackPage } from "./OAuthCallbackPage";
import { OAuthLoginMethods } from "./OAuthLoginMethods";

const iam: BaseAddon = {
  id: "iam",
  routes: [
    {
      name: "iam.login.callback",
      path: "/login/callback",
      shell: "public",
      component: OAuthCallbackPage,
    },
  ],
  slots: [
    {
      slot: AUTH_LOGIN_METHOD_SLOT,
      id: "iam.oauth-login",
      content: createElement(OAuthLoginMethods),
    },
  ],
};

export default iam;
