// @vitest-environment happy-dom
import { act, fireEvent, screen } from "@testing-library/react";
import type { RootOptions } from "react-dom/client";
import { afterEach, expect, test, vi } from "vitest";

import { bootApp } from "./boot-app";
import type { AngeeApp } from "./create-app";
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
  const create = vi.fn((): AngeeApp => { throw new Error("Home \"/files\" is outside menu root \"requests\"."); });
  const reload = vi.spyOn(window.location, "reload").mockImplementation(() => {});
  let boot!: Promise<void>;
  await act(async () => {
    boot = bootApp({ target: newHost(), loadSchemas: async () => ({}), create });
    await expect(boot).rejects.toThrow("is outside menu root");
  });
  expect(create).toHaveBeenCalledOnce();
  const alert = screen.getByRole("alert");
  expect(alert.textContent).toContain("Application could not start");
  expect(host.textContent).toContain('Home "/files" is outside menu root "requests".');
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
