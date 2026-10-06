import { AppBootSkeleton } from "@angee/ui/layouts/AppBootSkeleton";
import { ErrorBanner } from "@angee/ui/fragments/ErrorBanner";
import { useUiT } from "@angee/ui/i18n";
import { Button } from "@angee/ui/ui/button";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";

import type { AngeeApp } from "./create-app";
import { type ErrorReportingInput, startErrorReporting } from "./error-reporting";

export interface BootAppInput<Schemas> {
  target: string | Element;
  loadSchemas: () => Promise<Schemas>;
  create: (schemas: Schemas) => AngeeApp;
  /** Sentry DSN and environment; without a DSN no reporting SDK is loaded. */
  errorReporting?: ErrorReportingInput | undefined;
}

/** Load generated metadata before synchronous app composition and route creation. */
export async function bootApp<Schemas>({
  target,
  loadSchemas,
  create,
  errorReporting,
}: BootAppInput<Schemas>): Promise<void> {
  const found = typeof target === "string" ? document.querySelector(target) : target;
  if (!found) throw new Error(`bootApp: no element matched ${String(target)}`);
  const element: Element = found;
  // Loads alongside the metadata; resolves to no options when no DSN is configured.
  const rootOptions = startErrorReporting(errorReporting);
  const root = createRoot(element);
  let pending = false;

  async function run(): Promise<void> {
    if (pending) return;
    pending = true;
    root.render(<AppBootSkeleton />);
    let schemas: Schemas;
    try {
      schemas = await loadSchemas();
    } catch {
      pending = false;
      root.render(<MetadataLoadFailure retry={() => { void run().catch(reportError); }} />);
      return;
    }
    // Reporting is running before composition, so a start failure reaches it too.
    const options = await rootOptions;
    let app: AngeeApp;
    try {
      app = create(schemas);
    } catch (error) {
      // Invalid metadata, route collisions and shell misconfiguration are not
      // fetch failures: show why the app cannot start, and still let the caller
      // and development overlay see the error.
      root.render(<StartFailure error={error} />);
      throw error;
    }
    root.unmount();
    app.mount(element, options);
  }

  await run();
}

function MetadataLoadFailure({ retry }: { retry: () => void }) {
  const t = useUiT();
  return (
    <BootFailure
      title={t("app.loadFailed")}
      description={t("app.loadFailedDescription")}
      action={<Button type="button" size="sm" onClick={retry}>{t("app.retry")}</Button>}
    />
  );
}

/** The app's composition failed: name the cause, offer a reload once it is fixed. */
function StartFailure({ error }: { error: unknown }) {
  const t = useUiT();
  return (
    <BootFailure
      title={t("app.startFailed")}
      description={t("app.startFailedDescription")}
      action={<Button type="button" size="sm" onClick={() => window.location.reload()}>{t("app.reload")}</Button>}
    >
      <pre className="m-0 max-h-[50dvh] overflow-auto whitespace-pre-wrap break-words rounded-8 bg-inset p-3 font-mono text-12 text-fg-muted">
        {error instanceof Error ? error.message : String(error)}
      </pre>
    </BootFailure>
  );
}

function BootFailure({ title, description, action, children }: {
  title: string;
  description: string;
  action: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="grid min-h-dvh place-items-center bg-canvas p-6 text-fg">
      <div className="grid w-full max-w-lg gap-3">
        <ErrorBanner title={title} description={description} actions={action} />
        {children}
      </div>
    </div>
  );
}
