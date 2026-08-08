import { createApp, defineBaseAddon } from "@angee/app";
import { ConsoleLayout } from "@angee/ui";
import { IamLoginPage } from "@angee/iam";

import { composedAddons, schemas } from "../../runtime/web/app";
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
    // A public-keyed layout is unauthenticated by default (createApp owns
    // that), but the schema must be pinned: defaultSchema is "console", so the
    // public login layout points back to the public client explicitly.
    public: { schema: "public" },
  },
  schemas,
  defaultSchema: "console",
  home: "/parties",
}).mount("#root");

function LoginRoute() {
  return <IamLoginPage redirectTo="/parties" />;
}
