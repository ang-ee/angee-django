import {
  ConsoleShell,
  createApp,
  defineBaseAddon,
} from "@angee/base";
import notes from "@angee-example/notes-web";
import agents from "@angee/agents";
import iam, { IamLoginPage } from "@angee/iam";
import integrate from "@angee/integrate";
import knowledge from "@angee/knowledge";
import messaging from "@angee/messaging";
import operator from "@angee/operator";
import parties from "@angee/parties";
import platform from "@angee/platform";
import resources from "@angee/resources";
import storage from "@angee/storage";

import publicSDL from "../../runtime/schemas/public.graphql?raw";
import consoleSDL from "../../runtime/schemas/console.graphql?raw";
import publicMetadata from "../../runtime/schemas/public.metadata.json";
import consoleMetadata from "../../runtime/schemas/console.metadata.json";
import { DemoForgotPasswordHint } from "./demo-auth";
import "./index.css";

const authAddon = defineBaseAddon({
  id: "auth",
  routes: [
    {
      name: "auth.login",
      path: "/login",
      shell: "public",
      component: LoginRoute,
    },
  ],
});

createApp({
  // Platform apps cluster at the bottom of the rail (group: "platform"): IAM,
  // Integrate, then the Platform app. Operator and Resources contribute their
  // sections into Platform (parentId), so they carry no rail glyph of their own.
  addons: [notes, authAddon, iam, parties, messaging, integrate, agents, operator, storage, knowledge, resources, platform],
  shells: {
    console: { chrome: ConsoleShell },
    // Chrome defaults to PassthroughChrome and a public-keyed shell is
    // unauthenticated by default (createApp owns both), but the schema must be
    // pinned: defaultSchema is "console", so the public login shell points back
    // to the public client explicitly.
    public: { schema: "public" },
  },
  schemas: {
    public: { url: "/graphql/public/", sdl: publicSDL, metadata: publicMetadata },
    console: { url: "/graphql/console/", sdl: consoleSDL, metadata: consoleMetadata, live: true },
  },
  // The console is the primary surface, so it is the default schema; the public
  // login shell pins itself back to the public client above.
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
