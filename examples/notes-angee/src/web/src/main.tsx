import {
  AUTH_LOGIN_CARD_FOOTER_SLOT,
  ConsoleShell,
  LoginPage,
  createApp,
  type BaseAddon,
} from "@angee/base";
import { cacheConfigFromSDL } from "@angee/sdk";
import notes from "@angee-example/notes-web";
import operator from "@angee/operator";

import publicSDL from "../../runtime/schemas/public.graphql?raw";
import consoleSDL from "../../runtime/schemas/console.graphql?raw";
import { DemoCredentials } from "./demo-auth";
import "./index.css";

const authAddon: BaseAddon = {
  id: "auth",
  routes: [
    {
      name: "auth.login",
      path: "/login",
      shell: "public",
      component: () => <LoginPage redirectTo="/notes" />,
    },
  ],
};

createApp({
  addons: [notes, authAddon, operator],
  shells: {
    console: { chrome: ConsoleShell },
    public: { chrome: ({ children }) => children, requireAuth: false },
  },
  schemas: {
    public: { url: "/graphql/public/", cache: cacheConfigFromSDL(publicSDL) },
    console: { url: "/graphql/console/", cache: cacheConfigFromSDL(consoleSDL) },
  },
  slots: [
    {
      slot: AUTH_LOGIN_CARD_FOOTER_SLOT,
      id: "notes-demo-users",
      content: <DemoCredentials />,
    },
  ],
  home: "/notes",
}).mount("#root");
