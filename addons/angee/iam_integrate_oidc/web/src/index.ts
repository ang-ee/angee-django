import { defineBaseAddon } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { createElement } from "react";

import { enOidcMessages } from "./i18n";
import { OAuthLoginMethods } from "./OAuthLoginMethods";
import { LOGIN_CALLBACK_PATH } from "./redirects";
import { oidcLoginSection } from "./views/oidc-section";

const iamIntegrateOidc = defineBaseAddon({
  id: "iam-integrate-oidc",
  routes: [
    {
      name: "iam-integrate-oidc.login.callback",
      path: LOGIN_CALLBACK_PATH,
      layout: "public",
      component: lazyRouteComponent(() => import("./OAuthCallbackPage"), "OAuthCallbackPage"),
    },
  ],
  i18n: { "iam-integrate-oidc": enOidcMessages },
  containers: {
    "auth.login#method": {
      "iam-integrate-oidc.oauth-login": { content: createElement(OAuthLoginMethods) },
    },
    "integrate.OAuthClient#sections": {
      "iam-integrate-oidc.oidc-login": { sequence: 10, content: oidcLoginSection },
    },
  },
});

export default iamIntegrateOidc;
