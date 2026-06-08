import {
  AUTH_LOGIN_CARD_FOOTER_SLOT,
  ConsoleShell,
  LoginPage,
  createApp,
  type BaseAddon,
} from "@angee/base";
import notes from "@angee-example/notes-web";
import iam from "@angee/iam";
import operator from "@angee/operator";
import storage from "@angee/storage";

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
  addons: [notes, authAddon, iam, operator, storage],
  shells: {
    console: { chrome: ConsoleShell },
    // Chrome defaults to PassthroughChrome and a public-keyed shell is
    // unauthenticated by default (createApp owns both), but the schema must be
    // pinned: defaultSchema is "console", so the public login shell points back
    // to the public client explicitly.
    public: { schema: "public" },
  },
  schemas: {
    public: { url: "/graphql/public/", sdl: publicSDL },
    console: { url: "/graphql/console/", sdl: consoleSDL },
  },
  // The console is the primary surface, so it is the default schema; the public
  // login shell pins itself back to the public client above.
  defaultSchema: "console",
  slots: [
    {
      slot: AUTH_LOGIN_CARD_FOOTER_SLOT,
      id: "notes-demo-users",
      content: <DemoCredentials />,
    },
  ],
  home: "/notes",
}).mount("#root");
