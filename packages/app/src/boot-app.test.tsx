// @vitest-environment happy-dom
import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { bootApp } from "./boot-app";
import type { AngeeApp } from "./create-app";

let host: HTMLElement;

afterEach(() => {
  host?.remove();
  vi.unstubAllGlobals();
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
  expect(mount).toHaveBeenCalledExactlyOnceWith(host);
});

test("propagates a createApp error without showing a fetch retry", async () => {
  const create = vi.fn((): AngeeApp => { throw new Error("invalid metadata"); });
  await expect(bootApp({
    target: newHost(),
    loadSchemas: async () => ({}),
    create,
  })).rejects.toThrow("invalid metadata");
  expect(create).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
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
});
