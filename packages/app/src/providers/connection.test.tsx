// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ServerConnection } from "@angee/refine";
import { AppRuntimeProvider, containersFromChildren, createAngeeI18nInstance } from "@angee/ui/runtime";
import { afterEach, expect, test, vi } from "vitest";
import { CONNECTION_CONTAINERS, ConnectionBoundary, ConnectionLostDialog, createServerProbe } from "./connection";

vi.mock("@angee/logo-react", async importOriginal => ({
  ...(await importOriginal<typeof import("@angee/logo-react")>()),
  AngeeLogoCube: ({ animationType }: { animationType: string }) => <span data-testid="connection-cube" data-animation={animationType} />,
}));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

test("uses one bounded recovery schedule and retains the mounted page", async () => {
  vi.useFakeTimers();
  const connection = new ServerConnection();
  const queryClient = new QueryClient();
  const probe = vi.fn().mockRejectedValue(new TypeError("offline"));
  const onRestored = vi.fn();
  const onLost = vi.fn();
  render(<QueryClientProvider client={queryClient}>
    <ConnectionBoundary {...{ connection, queryClient, probe, onRestored, onLost }}>
      <input aria-label="Unsaved note" defaultValue="Still here" />
    </ConnectionBoundary>
  </QueryClientProvider>);
  const note = screen.getByLabelText("Unsaved note");
  await act(async () => { connection.disconnect(); });
  expect(screen.getByRole("dialog", { name: "Finding our way back" })).toBeTruthy();
  expect(probe).toHaveBeenCalledTimes(1);
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
  expect(connection.getSnapshot()).toBe(false);
  expect(onRestored).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("Unsaved note")).toBe(note);
  expect((note as HTMLInputElement).value).toBe("Still here");
  queryClient.clear();
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

test("the recovery probe is a bounded safe query and aborts with its observer", async () => {
  const controller = new AbortController();
  let signal: AbortSignal | null | undefined;
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    signal = init?.signal;
    return Response.json({ data: { __typename: "Query" } });
  });
  await createServerProbe({ url: "/graphql/public/", auth: base => base, fetch })(controller.signal);
  expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body)).query).toContain("query AngeeConnection");
  controller.abort();
  expect(signal?.aborted).toBe(true);
});
