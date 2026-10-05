import { bootApp, createApp, defineBaseAddon } from "@angee/app";
import { IamLoginPage } from "@angee/iam";
import { ConsoleLayout } from "@angee/ui";
import { appearance } from "virtual:angee-appearance";

import { composedAddons, loadComposedSchemas } from "../../runtime/web/app";

const create = (schemas: Awaited<ReturnType<typeof loadComposedSchemas>>) => createApp({
  addons: [
    defineBaseAddon({
      id: "host-auth",
      routes: [
        {
          name: "auth.login",
          path: "/login",
          layout: "public",
          component: IamLoginPage,
        },
      ],
    }),
    ...composedAddons,
  ],
  layouts: {
    console: { chrome: ConsoleLayout },
    public: { schema: "public" },
  },
  schemas,
  defaultSchema: "console",
  appearance,
});

await bootApp({
  target: "#root",
  loadSchemas: loadComposedSchemas,
  create,
  // Set by the stack's sentry_web_dsn input; empty leaves the Sentry SDK unloaded.
  errorReporting: {
    dsn: import.meta.env.VITE_SENTRY_DSN,
    environment: import.meta.env.VITE_SENTRY_ENVIRONMENT,
  },
});
