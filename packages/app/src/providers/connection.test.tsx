// @vitest-environment happy-dom
import { lazy, type ReactNode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, onlineManager } from "@tanstack/react-query";
import type { NotificationProvider } from "@refinedev/core";
import { ServerConnection, ServerUnavailableError } from "@angee/refine";
import { AppRuntimeProvider, containersFromChildren, createAngeeI18nInstance } from "@angee/ui/runtime";
import { afterEach, expect, test, vi } from "vitest";
import {
  CONNECTION_CONTAINERS,
  ConnectionBoundary,
  ConnectionLostDialog,
  createServerProbe,
  useConnectionNotifications,
} from "./connection";

vi.mock("@angee/logo-react", async importOriginal => ({
  ...(await importOriginal<typeof import("@angee/logo-react")>()),
  AngeeLogoCube: ({ animationType }: { animationType: string }) => <span data-testid="connection-cube" data-animation={animationType} />,
}));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function boundary(probe: (signal: AbortSignal) => Promise<void>) {
  const connection = new ServerConnection();
  const queryClient = new QueryClient();
  const onRestored = vi.fn();
  const onLost = vi.fn();
  render(<ConnectionBoundary {...{ connection, queryClient, probe, onRestored, onLost }}>
    <input aria-label="Unsaved note" defaultValue="Still here" />
  </ConnectionBoundary>);
  const offline = connection.fetch(async () => { throw new TypeError("Failed to fetch"); });
  const failRequest = () => act(async () => { await offline("/graphql/").catch(() => undefined); });
  return { connection, queryClient, onRestored, onLost, failRequest };
}

test("a failed check opens the dialog, retries on one bounded schedule and retains the mounted page", async () => {
  vi.useFakeTimers();
  const probe = vi.fn().mockRejectedValue(new TypeError("offline"));
  const { connection, queryClient, onRestored, onLost, failRequest } = boundary(probe);
  const note = screen.getByLabelText("Unsaved note");
  await failRequest();
  expect(probe).toHaveBeenCalledTimes(1);
  expect(connection.getSnapshot()).toBe("unavailable");
  expect(screen.getByRole("dialog", { name: "Finding our way back" })).toBeTruthy();
  expect(onLost).toHaveBeenCalledTimes(1);
  for (const delay of [5_000, 10_000, 20_000, 30_000, 30_000]) {
    const before = probe.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(delay - 1); });
    expect(probe).toHaveBeenCalledTimes(before);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(probe).toHaveBeenCalledTimes(before + 1);
  }
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(screen.getByRole("dialog")).toBeTruthy();
  probe.mockResolvedValue(undefined);
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(connection.getSnapshot()).toBe("connected");
  expect(onRestored).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("Unsaved note")).toBe(note);
  expect((note as HTMLInputElement).value).toBe("Still here");
  queryClient.clear();
});

/** A read fails before a request arms the probe, another while the probe checks; then the outage recovers. */
async function recoverAroundReads() {
  vi.useFakeTimers();
  let failProbe!: (error: Error) => void;
  const probe = vi.fn()
    .mockImplementationOnce(() => new Promise<void>((_resolve, reject) => { failProbe = reject; }))
    .mockResolvedValue(undefined);
  const { connection, queryClient, onRestored, failRequest } = boundary(probe);
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  const read = (key: string) => queryClient.query({
    queryKey: [key], retry: false, queryFn: () => Promise.reject(new Error("Request failed.")),
  }).catch(() => undefined);
  await read("before");
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  await failRequest();
  expect(connection.getSnapshot()).toBe("checking");
  await read("during");
  await act(async () => { failProbe(new TypeError("offline")); await vi.advanceTimersByTimeAsync(0); });
  expect(connection.getSnapshot()).toBe("unavailable");
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(connection.getSnapshot()).toBe("connected");
  expect(onRestored).toHaveBeenCalledTimes(1);
  expect(invalidate).toHaveBeenCalledTimes(1);
  const refreshes = invalidate.mock.calls[0]?.[0]?.predicate;
  return (key: string) => refreshes?.(queryClient.getQueryCache().find({ queryKey: [key] })!);
}

test("recovery refreshes a read that failed while the probe was checking", async () => {
  const refreshed = await recoverAroundReads();
  expect(refreshed("during")).toBe(true);
});

test("recovery leaves a read that failed before the probe was armed", async () => {
  const refreshed = await recoverAroundReads();
  expect(refreshed("before")).toBe(false);
});

test("a healthy check leaves the failure ordinary: no dialog, no lost or restored transition", async () => {
  const probe = vi.fn().mockResolvedValue(undefined);
  const { connection, onRestored, onLost, failRequest } = boundary(probe);
  await failRequest();
  await waitFor(() => expect(connection.getSnapshot()).toBe("connected"));
  expect(probe).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(onLost).not.toHaveBeenCalled();
  expect(onRestored).not.toHaveBeenCalled();
});

test("the browser's offline flag neither opens the dialog nor pauses the check", async () => {
  onlineManager.setOnline(false);
  try {
    const probe = vi.fn().mockResolvedValue(undefined);
    const { connection, failRequest } = boundary(probe);
    expect(screen.queryByRole("dialog")).toBeNull();
    await failRequest();
    await waitFor(() => expect(connection.getSnapshot()).toBe("connected"));
    expect(probe).toHaveBeenCalledTimes(1);
  } finally {
    onlineManager.setOnline(true);
  }
});

test("outage error toasts wait and a refused write is reported once the server is back", async () => {
  const connection = new ServerConnection();
  const queryClient = new QueryClient();
  const open = vi.fn();
  const notifications: NotificationProvider = { open, close: vi.fn() };
  let provider!: NotificationProvider;
  function Notifications(): ReactNode {
    provider = useConnectionNotifications(connection, queryClient, notifications);
    return null;
  }
  render(<Notifications />);
  act(() => { connection.disconnect(); });
  provider.open({ type: "error", message: "Something went wrong" });
  const write = queryClient.getMutationCache().build(queryClient, {
    mutationFn: async () => { throw new ServerUnavailableError(); },
  });
  await act(async () => { await write.execute(undefined).catch(() => undefined); });
  expect(open).not.toHaveBeenCalled();
  act(() => { connection.restore(); });
  expect(open).toHaveBeenCalledTimes(1);
  expect(open).toHaveBeenCalledWith(expect.objectContaining({ type: "error", message: "A change was not saved" }));
  act(() => { connection.disconnect(); connection.restore(); });
  provider.open({ type: "error", message: "Business error" });
  expect(open).toHaveBeenCalledTimes(2);
  expect(open).toHaveBeenLastCalledWith(expect.objectContaining({ message: "Business error" }));
});

test("customer containers and vocabulary replace the default artwork and copy", () => {
  render(<AppRuntimeProvider runtime={{
    i18n: createAngeeI18nInstance({ ui: { "connection.title": "Back with you shortly" } }),
    containers: containersFromChildren(CONNECTION_CONTAINERS, {
      "connection.lost#artwork": { "customer.orbit": { content: <span>Customer artwork</span> } },
      "connection.lost#help": { "customer.support": { content: <a href="https://status.example.test">Service status</a> } },
    }),
  }}><ConnectionLostDialog open /></AppRuntimeProvider>);
  expect(CONNECTION_CONTAINERS.map(container => container.address)).toEqual(["connection.lost#artwork", "connection.lost#help"]);
  expect(screen.getByRole("dialog", { name: "Back with you shortly" })).toBeTruthy();
  expect(screen.getByText("Customer artwork")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Service status" })).toBeTruthy();
  expect(screen.queryByTestId("connection-cube")).toBeNull();
});

test("a suspending or throwing contribution degrades without blanking the dialog", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const Pending = lazy(() => new Promise<never>(() => undefined));
  function NeedsRouter(): ReactNode { throw new Error("useRouter must be used inside a RouterProvider."); }
  render(<AppRuntimeProvider runtime={{
    containers: containersFromChildren(CONNECTION_CONTAINERS, {
      "connection.lost#artwork": { "customer.orbit": { content: <Pending /> } },
      "connection.lost#help": { "customer.support": { content: <NeedsRouter /> } },
    }),
  }}><ConnectionLostDialog open /></AppRuntimeProvider>);
  expect(screen.getByRole("dialog", { name: "Finding our way back" })).toBeTruthy();
  expect(screen.getByTestId("connection-cube")).toBeTruthy();
});

test("the brand mark replaces Angee artwork for a branded customer", () => {
  render(<AppRuntimeProvider runtime={{
    brand: { name: "Customer", mark: "customer" }, icons: { customer: () => <span>Customer mark</span> },
  }}><ConnectionLostDialog open /></AppRuntimeProvider>);
  expect(screen.getByText("Customer mark")).toBeTruthy();
  expect(screen.queryByTestId("connection-cube")).toBeNull();
});

test("reduced motion keeps the default cube still", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  render(<ConnectionLostDialog open />);
  expect(screen.getByTestId("connection-cube").getAttribute("data-animation")).toBe("none");
});

test("the recovery probe is a safe query that aborts with its observer", async () => {
  const controller = new AbortController();
  let signal: AbortSignal | null | undefined;
  const fetch = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
    signal = init?.signal;
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => { reject(init.signal?.reason); });
    });
  });
  const outcome = createServerProbe({ url: "/graphql/public/", auth: base => base, fetch })(controller.signal).catch(error => error);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body)).query).toContain("query AngeeConnection");
  controller.abort();
  expect(signal?.aborted).toBe(true);
  expect(await outcome).toBeInstanceOf(Error);
});

test("the recovery probe keeps its own deadline without AbortSignal.any and clears it", async () => {
  const any = Object.getOwnPropertyDescriptor(AbortSignal, "any");
  Object.defineProperty(AbortSignal, "any", { configurable: true, value: undefined });
  vi.useFakeTimers();
  try {
    let signal: AbortSignal | null | undefined;
    const fetch = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signal = init?.signal;
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => { reject(init.signal?.reason); });
      });
    });
    const outcome = createServerProbe({ url: "/graphql/public/", auth: base => base, fetch })(new AbortController().signal)
      .catch(error => error);
    await vi.advanceTimersByTimeAsync(9_999);
    expect(signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(signal?.aborted).toBe(true);
    expect(await outcome).toBeInstanceOf(Error);
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    if (any) Object.defineProperty(AbortSignal, "any", any);
  }
});
