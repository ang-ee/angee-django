import {
  ConsoleShell,
  LoginPage,
  createApp,
  defineBaseAddon,
} from "@angee/base";
import { useEffect, useState } from "react";
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

const LOGIN_BACKGROUND_ROTATION_MS = 15_000;

const loginBackgroundUrls = Object.entries(
  import.meta.glob<string>("../../../../assets/backgrounds/*.{avif,jpeg,jpg,png,webp}", {
    eager: true,
    import: "default",
    query: "?url",
  }),
)
  .sort(([left], [right]) => left.localeCompare(right))
  .map(([, url]) => url);

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

function LoginRoute() {
  const [backgroundImageUrl, setBackgroundImageUrl] = useState(() =>
    pickRandomLoginBackgroundUrl(loginBackgroundUrls),
  );

  useEffect(() => {
    if (loginBackgroundUrls.length < 2) return undefined;

    const interval = window.setInterval(() => {
      setBackgroundImageUrl((current) =>
        pickRandomLoginBackgroundUrl(loginBackgroundUrls, current),
      );
    }, LOGIN_BACKGROUND_ROTATION_MS);

    return () => window.clearInterval(interval);
  }, []);

  return (
    <LoginPage
      redirectTo="/notes"
      backgroundImageUrl={backgroundImageUrl}
      passwordHelp={<DemoForgotPasswordHint />}
    />
  );
}

function pickRandomLoginBackgroundUrl(
  urls: readonly string[],
  previousUrl?: string,
): string | undefined {
  if (urls.length === 0) return undefined;
  if (urls.length === 1) return urls[0];

  const candidates = previousUrl
    ? urls.filter((url) => url !== previousUrl)
    : urls;
  return candidates[Math.floor(Math.random() * candidates.length)] ?? urls[0];
}
