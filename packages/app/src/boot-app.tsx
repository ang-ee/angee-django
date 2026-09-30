import { AppBootSkeleton } from "@angee/ui/layouts/AppBootSkeleton";
import { ErrorBanner } from "@angee/ui/fragments/ErrorBanner";
import { useUiT } from "@angee/ui/i18n";
import { Button } from "@angee/ui/ui/button";
import { createRoot } from "react-dom/client";

import type { AngeeApp } from "./create-app";

export interface BootAppInput<Schemas> {
  target: string | Element;
  loadSchemas: () => Promise<Schemas>;
  create: (schemas: Schemas) => AngeeApp;
}

/** Load generated metadata before synchronous app composition and route creation. */
export async function bootApp<Schemas>({ target, loadSchemas, create }: BootAppInput<Schemas>): Promise<void> {
  const found = typeof target === "string" ? document.querySelector(target) : target;
  if (!found) throw new Error(`bootApp: no element matched ${String(target)}`);
  const element: Element = found;
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
    // Invalid metadata and route collisions are programming errors. Let the
    // caller and development overlay see them instead of offering fetch retry.
    const app = create(schemas);
    root.unmount();
    app.mount(element);
  }

  await run();
}

function MetadataLoadFailure({ retry }: { retry: () => void }) {
  const t = useUiT();
  return (
    <div className="grid min-h-dvh place-items-center bg-canvas p-6 text-fg">
      <ErrorBanner
        className="w-full max-w-lg"
        title={t("app.loadFailed")}
        description={t("app.loadFailedDescription")}
        actions={<Button type="button" size="sm" onClick={retry}>{t("app.retry")}</Button>}
      />
    </div>
  );
}
