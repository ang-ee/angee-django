/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Sentry DSN for the SPA (the stack's sentry_web_dsn input); unset leaves Sentry unloaded. */
  readonly VITE_SENTRY_DSN?: string;
  /** Sentry environment, set alongside the DSN from the stack's serve mode. */
  readonly VITE_SENTRY_ENVIRONMENT?: string;
}

declare module "*.graphql?raw" {
  const content: string;
  export default content;
}

declare module "virtual:angee-appearance" {
  import type { HostAppearanceDefaults } from "@angee/ui/theme";
  export const appearance: HostAppearanceDefaults;
  export default appearance;
}
