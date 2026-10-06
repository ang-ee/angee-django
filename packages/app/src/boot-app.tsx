import { AppBootSkeleton } from "@angee/ui/layouts/AppBootSkeleton";
import { ErrorBanner } from "@angee/ui/fragments/ErrorBanner";
import { useUiT } from "@angee/ui/i18n";
import type { ChromeMenuNode } from "@angee/ui/chrome/menu-tree";
import type { RuntimeSelection, RuntimeSelectionField } from "@angee/ui/runtime";
import { Button } from "@angee/ui/ui/button";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";

import type { AngeeApp } from "./create-app";
import type { ShellSettings } from "./define-addon";
import { type ErrorReportingInput, startErrorReporting } from "./error-reporting";
import { assertBrand } from "./menus";

/** A top-level menu root as selection reads it; its label and icon stand in for a brand it does not declare. */
export type SelectableRoot = Pick<ChromeMenuNode, "id" | "displayLabel" | "iconName" | "home" | "brand" | "theme">;

/** What selects the app: the page's `?app=` and hostname, `ANGEE_UI.shell` and the composed menu roots. */
export interface SelectAppInput {
  search?: string | undefined;
  hostname?: string | undefined;
  shell?: ShellSettings | undefined;
  roots: readonly SelectableRoot[];
}

/**
 * Select the app the page shows: `?app=<root id | apps name>`, else the
 * hostname's `ANGEE_UI.shell.hosts` entry, else nothing, which shows every root
 * with the shell's brand and theme. No choice is stored. A root id is the
 * one-root app; a named app takes its own brand, theme and home, else its first
 * rail root's. An unknown `?app=` is reported and falls back.
 */
export function selectApp({ search, hostname, shell = {}, roots }: SelectAppInput): RuntimeSelection {
  const diagnostics: string[] = [];
  const requested = new URLSearchParams(search ?? "").get("app");
  if (requested) {
    const selected = appSelection(requested, shell, roots, `?app=${requested}`);
    if (selected) return selected;
    diagnostics.push(`?app=${requested} names no menu root and no ANGEE_UI.shell.apps entry, so it selects nothing.`);
  }
  const host = hostname?.toLowerCase();
  const mapped = host !== undefined ? own(shell.hosts, host) : undefined;
  const hosted = mapped !== undefined ? appSelection(mapped, shell, roots, `ANGEE_UI.shell.hosts["${host}"]`) : undefined;
  if (hosted) return { ...hosted, diagnostics };
  return {
    app: null,
    rail: null,
    brand: shell.brand ?? null,
    ...(shell.theme !== undefined ? { theme: shell.theme } : {}),
    sources: {
      ...(shell.brand ? { brand: "ANGEE_UI.shell.brand" } : {}),
      ...(shell.theme !== undefined ? { theme: "ANGEE_UI.shell.theme" } : {}),
    },
    diagnostics,
  };
}

/**
 * The app `key` names, selected `by` a selector: a menu root id is the one-root
 * app, an `ANGEE_UI.shell.apps` name its declared rail. Unset facts come from
 * the first rail root, whose brand defaults to its label and icon; an unset
 * theme falls back to the shell's. Undefined when `key` names neither.
 */
export function appSelection(
  key: string,
  shell: ShellSettings,
  roots: readonly SelectableRoot[],
  by: string,
): RuntimeSelection | undefined {
  const rootOf = (id: string) => roots.find((root) => root.id === id);
  const app = own(shell.apps, key);
  if (app && rootOf(key)) throw new Error(`ANGEE_UI.shell.apps names "${key}", a menu root id; name the app apart from the roots.`);
  const declared = app ? `ANGEE_UI.shell.apps.${key}` : `menu root "${key}"`;
  const rail = app ? app.rail : rootOf(key) ? [key] : undefined;
  if (!rail) return undefined;
  const missing = rail.find((id) => !rootOf(id));
  if (missing !== undefined) throw new Error(`${declared} rail names "${missing}", which is not a top-level menu root.`);
  const first = rootOf(rail[0]!)!;
  const inherited = `menu root "${first.id}"`;
  const theme = app?.theme ?? first.theme ?? shell.theme;
  const home = app?.home ?? first.home;
  const sources: Partial<Record<RuntimeSelectionField, string>> = {
    app: by,
    rail: declared,
    brand: app?.brand ? declared : first.brand ? inherited : `${inherited} label and icon`,
  };
  if (theme !== undefined) sources.theme = app?.theme !== undefined ? declared : first.theme !== undefined ? inherited : "ANGEE_UI.shell.theme";
  if (home !== undefined) sources.home = app?.home !== undefined ? declared : inherited;
  return {
    app: key,
    rail,
    brand: app?.brand ?? first.brand ?? { name: first.displayLabel, mark: first.iconName },
    ...(theme !== undefined ? { theme } : {}),
    ...(home !== undefined ? { home } : {}),
    sources,
    diagnostics: [],
  };
}

/** Refuse a malformed `ANGEE_UI.shell`, which would otherwise select the wrong app silently. */
export function assertShellSettings(shell: unknown): asserts shell is ShellSettings {
  const mapping = (value: unknown, where: string, keys?: readonly string[]): Record<string, unknown> => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error(`${where} must be a mapping.`);
    const unknown = keys ? Object.keys(value).find((key) => !keys.includes(key)) : undefined;
    if (unknown !== undefined) throw new Error(`${where} has unknown key "${unknown}".`);
    return value as Record<string, unknown>;
  };
  const text = (value: unknown, where: string): void => {
    if (typeof value !== "string" || !value.trim()) throw new Error(`${where} must be a non-empty string.`);
  };
  const facts = (value: Record<string, unknown>, where: string): void => {
    if (value.brand !== undefined) assertBrand(where, value.brand);
    if (value.theme !== undefined) text(value.theme, `${where}.theme`);
  };
  const settings = mapping(shell, "ANGEE_UI.shell", ["brand", "theme", "apps", "hosts"]);
  facts(settings, "ANGEE_UI.shell");
  for (const [name, value] of Object.entries(mapping(settings.apps ?? {}, "ANGEE_UI.shell.apps"))) {
    const where = `ANGEE_UI.shell.apps.${name}`;
    const app = mapping(value, where, ["rail", "brand", "theme", "home"]);
    const rail = app.rail;
    if (!Array.isArray(rail) || !rail.length || !rail.every((id) => typeof id === "string" && id)) {
      throw new Error(`${where}.rail must be a non-empty list of menu root ids.`);
    }
    if (new Set(rail).size !== rail.length) throw new Error(`${where}.rail lists a root twice.`);
    facts(app, where);
    if (app.home !== undefined) text(app.home, `${where}.home`);
  }
  for (const [host, key] of Object.entries(mapping(settings.hosts ?? {}, "ANGEE_UI.shell.hosts"))) {
    if (host !== host.toLowerCase()) throw new Error(`ANGEE_UI.shell.hosts["${host}"] must be a lower-case hostname.`);
    text(key, `ANGEE_UI.shell.hosts["${host}"]`);
  }
}

/** A record's own entry; inherited names such as `constructor` select nothing. */
function own<T>(record: Readonly<Record<string, T>> | undefined, key: string): T | undefined {
  return record && Object.prototype.hasOwnProperty.call(record, key) ? record[key] : undefined;
}

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
