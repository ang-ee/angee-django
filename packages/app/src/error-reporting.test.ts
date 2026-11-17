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
    beforeBreadcrumb: expect.any(Function),
    beforeSend: expect.any(Function),
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

  expect(sentry.init).toHaveBeenCalledExactlyOnceWith({
    dsn: "https://key@sentry.example.invalid/1",
    beforeBreadcrumb: expect.any(Function),
    beforeSend: expect.any(Function),
  });
});

test("leaves URL fragments, which can carry a sign-in token, out of breadcrumbs and events", async () => {
  const startErrorReporting = await load();
  await startErrorReporting({ dsn: "https://key@sentry.example.invalid/1" });
  const [options] = sentry.init.mock.calls[0] ?? [];
  const { beforeBreadcrumb, beforeSend } = options as {
    beforeBreadcrumb: (breadcrumb: object) => { data?: Record<string, unknown> };
    beforeSend: (event: object) => { request?: { url?: string; method?: string } };
  };

  expect(beforeBreadcrumb({
    category: "navigation",
    data: { from: "/sign-in-link#token=usr_abc.secret", to: "/sign-in-link" },
  }).data).toEqual({ from: "/sign-in-link", to: "/sign-in-link" });
  expect(beforeBreadcrumb({ category: "fetch", data: { url: "/graphql/public/", method: "POST" } }).data)
    .toEqual({ url: "/graphql/public/", method: "POST" });
  expect(beforeBreadcrumb({ category: "console", message: "kept" })).toEqual({ category: "console", message: "kept" });
  expect(beforeSend({ request: { url: "https://app.example.test/sign-in-link#token=usr_abc.secret", method: "GET" } }).request)
    .toEqual({ url: "https://app.example.test/sign-in-link", method: "GET" });
  expect(beforeSend({ message: "no request" })).toEqual({ message: "no request" });
});

test("boots without reporting when the SDK cannot start", async () => {
  const startErrorReporting = await load();
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  sentry.init.mockImplementationOnce(() => { throw new Error("blocked"); });

  await expect(startErrorReporting({ dsn: "https://key@sentry.example.invalid/1" })).resolves.toEqual({});
  expect(warn).toHaveBeenCalledOnce();
});
