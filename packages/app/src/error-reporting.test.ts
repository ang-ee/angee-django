import { beforeEach, expect, test, vi } from "vitest";

const sentry = vi.hoisted(() => {
  const handler = vi.fn();
  return { loads: 0, handler, init: vi.fn(), reactErrorHandler: vi.fn((_callback?: unknown) => handler) };
});

vi.mock("@sentry/react", () => {
  sentry.loads += 1;
  return { init: sentry.init, reactErrorHandler: sentry.reactErrorHandler };
});

/** A fresh module graph per test, so each test sees whether the SDK chunk was loaded. */
async function load() {
  return (await import("./error-reporting")).startErrorReporting;
}

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  sentry.loads = 0;
});

test.each([undefined, {}, { dsn: "" }, { dsn: undefined, environment: "production" }])(
  "never loads the Sentry SDK and leaves React's handlers alone without a DSN (%o)",
  async (input) => {
    const startErrorReporting = await load();

    await expect(startErrorReporting(input)).resolves.toEqual({});
    expect(sentry.loads).toBe(0);
    expect(sentry.init).not.toHaveBeenCalled();
  },
);

test("starts Sentry with the DSN and environment and reports every React root error", async () => {
  const startErrorReporting = await load();

  const options = await startErrorReporting({ dsn: "https://key@sentry.example.invalid/1", environment: "production" });

  expect(sentry.loads).toBe(1);
  expect(sentry.init).toHaveBeenCalledExactlyOnceWith({
    dsn: "https://key@sentry.example.invalid/1",
    environment: "production",
  });
  expect(options).toEqual({
    onUncaughtError: sentry.handler,
    onCaughtError: sentry.handler,
    onRecoverableError: sentry.handler,
  });
});

test("keeps React's console output beside the report", async () => {
  const startErrorReporting = await load();
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  await startErrorReporting({ dsn: "https://key@sentry.example.invalid/1" });
  const [callback] = sentry.reactErrorHandler.mock.calls[0] ?? [];
  const failure = new Error("render failed");

  (callback as (error: unknown) => void)(failure);

  expect(consoleError).toHaveBeenCalledExactlyOnceWith(failure);
});

test("omits an empty environment so the SDK keeps its default", async () => {
  const startErrorReporting = await load();

  await startErrorReporting({ dsn: "https://key@sentry.example.invalid/1", environment: "" });

  expect(sentry.init).toHaveBeenCalledExactlyOnceWith({ dsn: "https://key@sentry.example.invalid/1" });
});

test("boots without reporting when the SDK cannot start", async () => {
  const startErrorReporting = await load();
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  sentry.init.mockImplementationOnce(() => { throw new Error("blocked"); });

  await expect(startErrorReporting({ dsn: "https://key@sentry.example.invalid/1" })).resolves.toEqual({});
  expect(warn).toHaveBeenCalledOnce();
});
