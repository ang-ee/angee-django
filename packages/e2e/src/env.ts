import type { PlaywrightTestConfig } from "@playwright/test";

const LOCALHOST = "127.0.0.1";

type ConnectOptions = NonNullable<PlaywrightTestConfig["use"]>["connectOptions"];

/**
 * The SPA origin the browser drives, read from the angee workspace environment.
 *
 * A workspace allocates a unique `ui` port and exports it as `ANGEE_UI_PORT`;
 * the Vite frontend serves on it and the harness targets it, so one config
 * drives every workspace without edits. `E2E_BASE_URL` overrides the derivation
 * outright (e.g. a remote preview deployment).
 */
export function resolveBaseURL(env: NodeJS.ProcessEnv = process.env): string {
  if (env.E2E_BASE_URL) return env.E2E_BASE_URL;
  const port = env.ANGEE_UI_PORT ?? "5173";
  return `http://${LOCALHOST}:${port}`;
}

/**
 * The remote Playwright browser-server connection, when one is configured.
 *
 * `E2E_WS_ENDPOINT` is the browser-server websocket URL. An optional
 * `E2E_WS_TOKEN` is sent as a bearer token for an operator forward-auth edge.
 * Without an endpoint, Playwright launches its browser locally as usual.
 */
export function resolveConnectOptions(
  env: NodeJS.ProcessEnv = process.env,
): ConnectOptions {
  if (!env.E2E_WS_ENDPOINT) return undefined;
  return {
    wsEndpoint: env.E2E_WS_ENDPOINT,
    ...(env.E2E_WS_TOKEN
      ? { headers: { Authorization: `Bearer ${env.E2E_WS_TOKEN}` } }
      : {}),
  };
}
