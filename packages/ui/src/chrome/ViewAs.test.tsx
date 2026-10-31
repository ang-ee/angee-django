// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AppRuntimeProvider, type RuntimeAuthState } from "../runtime";
import { createUiTestProviders } from "../testing";
import { ViewAsBanner, ViewAsPicker } from "./ViewAs";

const user = { id: "reviewer", name: "Reviewer" };
const auth: RuntimeAuthState = {
  user, status: "authenticated", hasRole: () => false,
  viewAs: { viewAs: null, currentUser: user, realUser: null, enter: vi.fn(), exit: vi.fn() },
};

afterEach(cleanup);

test("a pending candidate read shows the picker and a successful empty list hides it", async () => {
  let release!: (value: { data: { viewable_people: { id: string; name: string }[] } }) => void;
  const response = new Promise<{ data: { viewable_people: { id: string; name: string }[] } }>((resolve) => { release = resolve; });
  const custom = vi.fn(() => response);
  const providers = createUiTestProviders({ dataProvider: { custom } });
  const mounted = render(<providers.Provider><AppRuntimeProvider runtime={{ auth }}><ViewAsPicker /></AppRuntimeProvider></providers.Provider>);
  try {
    expect(screen.getByRole("combobox", { name: "Preview as a person" })).toBeTruthy();
    await waitFor(() => expect(custom).toHaveBeenCalledOnce());
    await act(async () => { release({ data: { viewable_people: [] } }); });
    await waitFor(() => expect(screen.queryByRole("combobox")).toBeNull());
  } finally { mounted.unmount(); providers.clearClients(); }
});

test("a candidate error remains local to the picker and exposes the native retry", async () => {
  const custom = vi.fn()
    .mockRejectedValueOnce(new Error("Candidate read unavailable"))
    .mockResolvedValue({ data: { viewable_people: [user] } });
  const providers = createUiTestProviders({ dataProvider: { custom }, queryClientConfig: { defaultOptions: { queries: { retry: false } } } });
  const mounted = render(<providers.Provider><AppRuntimeProvider runtime={{ auth }}><ViewAsBanner /><ViewAsPicker /></AppRuntimeProvider></providers.Provider>);
  try {
    await screen.findByText("Candidate read unavailable");
    fireEvent.click(screen.getByRole("combobox", { name: "Preview as a person" }));
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await screen.findByRole("option", { name: "Reviewer" });
    expect(custom).toHaveBeenCalledTimes(2);
  } finally { mounted.unmount(); providers.clearClients(); }
});

test("an anonymous runtime does not request or offer preview candidates", () => {
  const custom = vi.fn(async () => ({ data: { viewable_people: [user] } }));
  const providers = createUiTestProviders({ dataProvider: { custom } });
  const mounted = render(<providers.Provider><AppRuntimeProvider runtime={{ auth: { user: null, status: "anonymous", hasRole: () => false } }}><ViewAsPicker /></AppRuntimeProvider></providers.Provider>);
  try {
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(custom).not.toHaveBeenCalled();
  } finally { mounted.unmount(); providers.clearClients(); }
});
