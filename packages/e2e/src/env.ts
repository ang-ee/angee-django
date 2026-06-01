const LOCALHOST = "127.0.0.1";

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
