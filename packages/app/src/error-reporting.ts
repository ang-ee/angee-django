import type { Breadcrumb, ErrorEvent } from "@sentry/react";
import type { RootOptions } from "react-dom/client";

/** Host error-reporting settings. An empty or missing DSN keeps the Sentry SDK unloaded. */
export interface ErrorReportingInput {
  readonly dsn?: string | undefined;
  readonly environment?: string | undefined;
}

/** `url` without its fragment, which can carry a credential such as a sign-in link's token. */
function withoutFragment(url: string): string {
  const index = url.indexOf("#");
  return index < 0 ? url : url.slice(0, index);
}

/** Drop URL fragments from a breadcrumb's navigation (`from`, `to`) and request (`url`) data. */
function scrubBreadcrumb(breadcrumb: Breadcrumb): Breadcrumb {
  if (!breadcrumb.data) return breadcrumb;
  const data = { ...breadcrumb.data };
  for (const key of ["from", "to", "url"]) {
    const value: unknown = data[key];
    if (typeof value === "string") data[key] = withoutFragment(value);
  }
  return { ...breadcrumb, data };
}

/** Drop the URL fragment from the page address an event records. */
function scrubEvent(event: ErrorEvent): ErrorEvent {
  const url = event.request?.url;
  return typeof url === "string" ? { ...event, request: { ...event.request, url: withoutFragment(url) } } : event;
}

/**
 * Start Sentry for a configured DSN and resolve the React root options that
 * report uncaught, caught and recoverable render errors to it.
 *
 * Without a DSN the SDK chunk is never imported and React keeps its default
 * handlers. A failure to load or start the SDK is reported to the console and
 * never stops the application from booting. Every app's reports leave URL
 * fragments out of navigation breadcrumbs and event addresses.
 */
export async function startErrorReporting(input: ErrorReportingInput | undefined): Promise<RootOptions> {
  if (!input?.dsn) return {};
  try {
    const Sentry = await import("@sentry/react");
    Sentry.init({
      dsn: input.dsn,
      ...(input.environment ? { environment: input.environment } : {}),
      beforeBreadcrumb: scrubBreadcrumb,
      beforeSend: scrubEvent,
    });
    // Replacing React's handlers would silence its console output; keep it beside the report.
    const report = Sentry.reactErrorHandler((error: unknown) => { console.error(error); });
    return { onUncaughtError: report, onCaughtError: report, onRecoverableError: report };
  } catch (error) {
    console.warn("[angee] Error reporting is unavailable; the app continues without it.", error);
    return {};
  }
}
