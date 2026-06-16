import {
  ConsoleShell,
  LoginPage,
  createApp,
  defineBaseAddon,
} from "@angee/base";
import notes from "@angee-example/notes-web";
import agents from "@angee/agents";
import iam from "@angee/iam";
import integrate from "@angee/integrate";
import knowledge from "@angee/knowledge";
import operator from "@angee/operator";
import storage from "@angee/storage";

import publicSDL from "../../runtime/schemas/public.graphql?raw";
import consoleSDL from "../../runtime/schemas/console.graphql?raw";
import { DemoForgotPasswordHint } from "./demo-auth";
import "./index.css";

const loginBackgroundUrls = Object.entries(
  import.meta.glob<string>("../../../../assets/backgrounds/*.{avif,jpeg,jpg,png,webp}", {
    eager: true,
    import: "default",
    query: "?url",
  }),
)
  .sort(([left], [right]) => left.localeCompare(right))
  .map(([, url]) => url);

const loginBackgroundImageUrl = pickRandomLoginBackgroundUrl(loginBackgroundUrls);

const authAddon = defineBaseAddon({
  id: "auth",
  routes: [
    {
      name: "auth.login",
      path: "/login",
      shell: "public",
      component: () => (
        <LoginPage
          redirectTo="/notes"
          backgroundImageUrl={loginBackgroundImageUrl}
          passwordHelp={<DemoForgotPasswordHint />}
        />
      ),
    },
  ],
});

createApp({
  addons: [notes, authAddon, iam, integrate, agents, operator, storage, knowledge],
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
  home: "/notes",
}).mount("#root");

function pickRandomLoginBackgroundUrl(urls: readonly string[]): string | undefined {
  if (urls.length === 0) return undefined;
  return urls[Math.floor(Math.random() * urls.length)];
}
