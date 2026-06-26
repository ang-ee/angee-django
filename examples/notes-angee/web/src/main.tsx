import { createApp, defineBaseAddon } from "@angee/app";
import { ConsoleLayout } from "@angee/ui";
import { IamLoginPage } from "@angee/iam";

import { composedAddons, schemas } from "../../runtime/web/app";
import { DemoForgotPasswordHint } from "./demo-auth";
import "./index.css";

const authAddon = defineBaseAddon({
  id: "auth",
  routes: [
    {
      name: "auth.login",
      path: "/login",
      layout: "public",
      component: LoginRoute,
    },
  ],
});

createApp({
  addons: [...composedAddons, authAddon],
  layouts: {
    console: { chrome: ConsoleLayout },
    // Chrome defaults to PassthroughChrome and a public-keyed layout is
    // unauthenticated by default (createApp owns both), but the schema must be
    // pinned: defaultSchema is "console", so the public login layout points back
    // to the public client explicitly.
    public: { schema: "public" },
  },
  schemas,
  // The console is the primary surface, so it is the default schema; the public
  // login layout pins itself back to the public client above.
  defaultSchema: "console",
  home: "/notes",
}).mount("#root");

function LoginRoute() {
  return (
    <IamLoginPage
      redirectTo="/notes"
      passwordHelp={<DemoForgotPasswordHint />}
    />
  );
}
