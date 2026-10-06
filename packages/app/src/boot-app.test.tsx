// @vitest-environment happy-dom
import { act, fireEvent, screen } from "@testing-library/react";
import type { RootOptions } from "react-dom/client";
import { afterEach, describe, expect, test, vi } from "vitest";

import { assertShellSettings, bootApp, selectApp, type SelectableRoot } from "./boot-app";
import type { AngeeApp } from "./create-app";
import type { ShellSettings } from "./define-addon";
import { startErrorReporting } from "./error-reporting";

const reportingOptions = vi.hoisted((): RootOptions => ({ onUncaughtError: () => {} }));

vi.mock("./error-reporting", () => ({
  startErrorReporting: vi.fn(async (input?: { dsn?: string | undefined }) => (input?.dsn ? reportingOptions : {})),
}));

let host: HTMLElement;

afterEach(() => {
  host?.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

function newHost() {
  host = document.createElement("div");
  document.body.append(host);
  return host;
}

test("shows a neutral skeleton, then retries only a failed metadata load", async () => {
  const schemas = { public: { metadata: {} } };
  let rejectFirst!: (reason?: unknown) => void;
  const first = new Promise<typeof schemas>((_resolve, reject) => { rejectFirst = reject; });
  const loadSchemas = vi.fn()
    .mockReturnValueOnce(first)
    .mockResolvedValueOnce(schemas);
  const mount = vi.fn();
  const create = vi.fn(() => ({ mount }) as unknown as AngeeApp);

  const target = newHost();
  let boot!: Promise<void>;
  await act(async () => {
    boot = bootApp({ target, loadSchemas, create });
  });
  expect(screen.getByRole("status").textContent).toContain("Loading application");
  expect(host.querySelectorAll('[aria-hidden="true"]')).toHaveLength(2);

  await act(async () => {
    rejectFirst(new Error("network unavailable"));
    await boot;
  });
  expect(screen.getByRole("alert").textContent).toContain("Application data is unavailable");
  expect(create).not.toHaveBeenCalled();

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  });
  expect(loadSchemas).toHaveBeenCalledTimes(2);
  expect(create).toHaveBeenCalledExactlyOnceWith(schemas);
  expect(mount).toHaveBeenCalledExactlyOnceWith(host, {});
});

test("shows why a createApp error stops the app and still propagates it, without a fetch retry", async () => {
  const create = vi.fn((): AngeeApp => { throw new Error("Menu root \"requests\" home \"files.all\" lies outside its rail (requests)."); });
  const reload = vi.spyOn(window.location, "reload").mockImplementation(() => {});
  let boot!: Promise<void>;
  await act(async () => {
    boot = bootApp({ target: newHost(), loadSchemas: async () => ({}), create });
    await expect(boot).rejects.toThrow("lies outside its rail");
  });
  expect(create).toHaveBeenCalledOnce();
  const alert = screen.getByRole("alert");
  expect(alert.textContent).toContain("Application could not start");
  expect(host.textContent).toContain('Menu root "requests" home "files.all" lies outside its rail (requests).');
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Reload" }));
  expect(reload).toHaveBeenCalledOnce();
});

test("reports a create error after Retry without returning to fetch failure", async () => {
  const error = new Error("invalid metadata");
  const report = vi.fn();
  vi.stubGlobal("reportError", report);
  const loadSchemas = vi.fn()
    .mockRejectedValueOnce(new Error("network unavailable"))
    .mockResolvedValueOnce({});
  const create = vi.fn((): AngeeApp => { throw error; });

  const target = newHost();
  await act(async () => {
    await bootApp({ target, loadSchemas, create });
  });
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  });
  expect(loadSchemas).toHaveBeenCalledTimes(2);
  expect(create).toHaveBeenCalledOnce();
  expect(report).toHaveBeenCalledExactlyOnceWith(error);
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  expect(screen.getByRole("alert").textContent).toContain("Application could not start");
});

test("waits for error reporting before composing and gives the app root its handlers", async () => {
  let resolveReporting!: (options: RootOptions) => void;
  vi.mocked(startErrorReporting).mockReturnValueOnce(new Promise((resolve) => { resolveReporting = resolve; }));
  const mount = vi.fn();
  const create = vi.fn(() => ({ mount }) as unknown as AngeeApp);
  const errorReporting = { dsn: "https://key@sentry.example.invalid/1", environment: "production" };

  const target = newHost();
  let boot!: Promise<void>;
  await act(async () => {
    boot = bootApp({ target, loadSchemas: async () => ({}), create, errorReporting });
  });
  expect(startErrorReporting).toHaveBeenCalledExactlyOnceWith(errorReporting);
  expect(create).not.toHaveBeenCalled();

  await act(async () => {
    resolveReporting(reportingOptions);
    await boot;
  });
  expect(create).toHaveBeenCalledOnce();
  expect(mount).toHaveBeenCalledExactlyOnceWith(host, reportingOptions);
});

const roots: readonly SelectableRoot[] = [
  { id: "pm", displayLabel: "Work", iconName: "pm", home: "projects.my-work" },
  { id: "accounting", displayLabel: "Accounting", iconName: "accounting", brand: { name: "Books", mark: "ledger" }, theme: "books.paper" },
  { id: "files", displayLabel: "Files", iconName: "files" },
];
const shell: ShellSettings = {
  brand: { name: "Suite", mark: "suite" },
  theme: "suite.light",
  apps: {
    payables: { rail: ["accounting", "files"], brand: { name: "Payables", mark: "payables" }, home: "accounting.bills" },
    library: { rail: ["files", "pm"] },
  },
  hosts: { "payables.example.test": "payables", "work.example.test": "pm" },
};

describe("selectApp", () => {
  test("?app= wins over the hostname, which wins over nothing; no choice is stored", () => {
    expect(selectApp({ search: "?app=pm", hostname: "payables.example.test", shell, roots }).app).toBe("pm");
    expect(selectApp({ search: "?tab=one", hostname: "payables.example.test", shell, roots }).app).toBe("payables");
    expect(selectApp({ search: "?app=", hostname: "elsewhere.example.test", shell, roots }).app).toBeNull();
    expect(selectApp({ shell, roots }).app).toBeNull();
  });

  test("a bare root id is the one-root app with the root's home, brand and theme", () => {
    expect(selectApp({ search: "?app=accounting", shell, roots })).toEqual({
      app: "accounting",
      rail: ["accounting"],
      brand: { name: "Books", mark: "ledger" },
      theme: "books.paper",
      sources: { app: "?app=accounting", rail: 'menu root "accounting"', brand: 'menu root "accounting"', theme: 'menu root "accounting"' },
      diagnostics: [],
    });
    // A root without a brand shows its label and icon; without a theme, the deployment's.
    expect(selectApp({ search: "", hostname: "work.example.test", shell, roots })).toEqual({
      app: "pm",
      rail: ["pm"],
      brand: { name: "Work", mark: "pm" },
      theme: "suite.light",
      home: "projects.my-work",
      sources: {
        app: 'ANGEE_UI.shell.hosts["work.example.test"]',
        rail: 'menu root "pm"',
        brand: 'menu root "pm" label and icon',
        theme: "ANGEE_UI.shell.theme",
        home: 'menu root "pm"',
      },
      diagnostics: [],
    });
  });

  test("a named app takes its own brand, theme and home, else its first rail root's", () => {
    expect(selectApp({ hostname: "payables.example.test", shell, roots })).toMatchObject({
      app: "payables",
      rail: ["accounting", "files"],
      brand: { name: "Payables", mark: "payables" },
      theme: "books.paper",
      home: "accounting.bills",
      sources: { brand: "ANGEE_UI.shell.apps.payables", theme: 'menu root "accounting"', home: "ANGEE_UI.shell.apps.payables" },
    });
    const library = selectApp({ search: "?app=library", shell, roots });
    expect(library).toMatchObject({ app: "library", rail: ["files", "pm"], brand: { name: "Files", mark: "files" }, theme: "suite.light" });
    // Its first rail root declares no home, so `/` lands on that root.
    expect(library).not.toHaveProperty("home");
  });

  test("nothing selected shows every root with the deployment's brand and theme, else none", () => {
    expect(selectApp({ search: "", hostname: "elsewhere.example.test", shell, roots })).toEqual({
      app: null,
      rail: null,
      brand: { name: "Suite", mark: "suite" },
      theme: "suite.light",
      sources: { brand: "ANGEE_UI.shell.brand", theme: "ANGEE_UI.shell.theme" },
      diagnostics: [],
    });
    expect(selectApp({ roots })).toEqual({ app: null, rail: null, brand: null, sources: {}, diagnostics: [] });
  });

  test("an unknown ?app= is reported and falls back to the host, then to nothing", () => {
    const unknown = "?app=nowhere names no menu root and no ANGEE_UI.shell.apps entry, so it selects nothing.";
    expect(selectApp({ search: "?app=nowhere", hostname: "work.example.test", shell, roots }))
      .toMatchObject({ app: "pm", diagnostics: [unknown] });
    expect(selectApp({ search: "?app=nowhere", shell, roots })).toMatchObject({ app: null, rail: null, diagnostics: [unknown] });
  });

  test("a hostname matches in lower case, and inherited names select nothing", () => {
    expect(selectApp({ hostname: "Payables.Example.TEST", shell, roots }).app).toBe("payables");
    expect(selectApp({ search: "?app=constructor", hostname: "constructor", shell, roots })).toMatchObject({ app: null, rail: null });
  });

  test("a rail naming no top-level root, or an app named like a root, fails", () => {
    expect(() => selectApp({ search: "?app=broken", shell: { apps: { broken: { rail: ["pm", "projects"] } } }, roots }))
      .toThrow('ANGEE_UI.shell.apps.broken rail names "projects", which is not a top-level menu root.');
    expect(() => selectApp({ search: "?app=files", shell: { apps: { files: { rail: ["pm"] } } }, roots }))
      .toThrow('ANGEE_UI.shell.apps names "files", a menu root id; name the app apart from the roots.');
  });
});

describe("assertShellSettings", () => {
  test("accepts ANGEE_UI.shell and refuses what would select the wrong app silently", () => {
    expect(() => assertShellSettings(shell)).not.toThrow();
    expect(() => assertShellSettings({})).not.toThrow();
    expect(() => assertShellSettings(["pm"])).toThrow("ANGEE_UI.shell must be a mapping.");
    expect(() => assertShellSettings({ perspective: "pm" })).toThrow('ANGEE_UI.shell has unknown key "perspective".');
    expect(() => assertShellSettings({ brand: { name: "Suite" } }))
      .toThrow("ANGEE_UI.shell: brand must be { name, mark } with a non-empty name and mark.");
    expect(() => assertShellSettings({ theme: " " })).toThrow("ANGEE_UI.shell.theme must be a non-empty string.");
    expect(() => assertShellSettings({ apps: { ap: { rail: "accounting" } } }))
      .toThrow("ANGEE_UI.shell.apps.ap.rail must be a non-empty list of menu root ids.");
    expect(() => assertShellSettings({ apps: { ap: { rail: ["accounting", "accounting"] } } }))
      .toThrow("ANGEE_UI.shell.apps.ap.rail lists a root twice.");
    expect(() => assertShellSettings({ apps: { ap: { rail: ["accounting"], hosts: {} } } }))
      .toThrow('ANGEE_UI.shell.apps.ap has unknown key "hosts".');
    expect(() => assertShellSettings({ apps: { ap: { rail: ["accounting"], home: "" } } }))
      .toThrow("ANGEE_UI.shell.apps.ap.home must be a non-empty string.");
    expect(() => assertShellSettings({ hosts: { "ap.example.test": ["accounting"] } }))
      .toThrow('ANGEE_UI.shell.hosts["ap.example.test"] must be a non-empty string.');
    expect(() => assertShellSettings({ hosts: { "AP.example.test": "accounting" } }))
      .toThrow('ANGEE_UI.shell.hosts["AP.example.test"] must be a lower-case hostname.');
  });
});
