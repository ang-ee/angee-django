import type { RootOptions } from "react-dom/client";

/** Host error-reporting settings. An empty or missing DSN keeps the Sentry SDK unloaded. */
export interface ErrorReportingInput {
  readonly dsn?: string | undefined;
  readonly environment?: string | undefined;
}

/**
 * Start Sentry for a configured DSN and resolve the React root options that
 * report uncaught, caught and recoverable render errors to it.
 *
 * Without a DSN the SDK chunk is never imported and React keeps its default
 * handlers. A failure to load or start the SDK is reported to the console and
 * never stops the application from booting.
 */
export async function startErrorReporting(input: ErrorReportingInput | undefined): Promise<RootOptions> {
  if (!input?.dsn) return {};
  try {
    const Sentry = await import("@sentry/react");
    Sentry.init({ dsn: input.dsn, ...(input.environment ? { environment: input.environment } : {}) });
    // Replacing React's handlers would silence its console output; keep it beside the report.
    const report = Sentry.reactErrorHandler((error: unknown) => { console.error(error); });
    return { onUncaughtError: report, onCaughtError: report, onRecoverableError: report };
  } catch (error) {
    console.warn("[angee] Error reporting is unavailable; the app continues without it.", error);
    return {};
  }
}
