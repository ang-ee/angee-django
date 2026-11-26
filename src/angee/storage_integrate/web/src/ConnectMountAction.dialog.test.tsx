// @vitest-environment happy-dom

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets } from "@angee/ui";
import { useState, type ReactElement, type ReactNode } from "react";
import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const account = (id: string, display_name: string) => ({ id, display_name, blocked_reason: null });
  return {
    connect: vi.fn(),
    facts: { data: { mount_backend: {
      key: "remote_source", requires_credential: true, oauth_client: null, oauth_scopes: [],
      accounts: [account("crd_1", "ada@example.com"), account("crd_2", "bob@example.com")],
    } }, refetch: () => undefined },
    targets: { data: { backends: [{ id: "bkd_2", label: "Archive", writable: true, is_archived: false }] } },
    browse: { data: { browse_mount_source: {
      location: { token: "/", label: "Root", is_navigable: true, is_mountable: true, blocked_reason: "" },
      parent_token: null, entries: [], truncated: false, supports_manual_token: false,
    } }, fetching: false, isFetching: false, error: null },
  };
});

vi.mock("./documents", () => ({
  BrowseMountSource: "BrowseMountSource",
  ConnectMount: "ConnectMount",
  MountBackendFacts: "MountBackendFacts",
  MountCopyTargets: "MountCopyTargets",
  MOUNT_MODEL: "storage_integrate.Mount",
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/refine")>(),
  useAuthoredQuery: (document: unknown) => document === "MountBackendFacts" ? mocks.facts
    : document === "MountCopyTargets" ? mocks.targets : mocks.browse,
  useAuthoredMutation: () => [mocks.connect, { fetching: false, error: null }],
}));

import { ConnectMountAction } from "./ConnectMountAction";

describe("ConnectMountAction's dialog", () => {
  beforeAll(() => {
    Object.defineProperty(globalThis, "ResizeObserver", {
      configurable: true,
      value: class ResizeObserver { observe(): void {} unobserve(): void {} disconnect(): void {} },
    });
    Element.prototype.scrollIntoView = vi.fn();
  });

  beforeEach(() => {
    mocks.connect.mockReset();
  });

  test("another account clears the chosen folder and shows no message on it", async () => {
    await openWithFolder();
    expect(useThisFolder().getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "bob@example.com" }));
    await settle();
    expect(useThisFolder().getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("group", { name: "Source folder" }).getAttribute("aria-invalid")).toBeNull();
    expect(screen.queryByText("This field is required.")).toBeNull();
    expect((screen.getByRole("button", { name: "Connect" }) as HTMLButtonElement).disabled).toBe(true);
  });

  test("a copy target hidden by leaving files in place is not submitted, and comes back as its initial value", async () => {
    mocks.connect.mockRejectedValueOnce(new Error("Refused"));
    await openWithFolder();
    await choose("File handling", "Copy files");
    const initialTarget = screen.getByRole("combobox", { name: "Copy into" }).textContent;
    expect(initialTarget).not.toContain("Archive");
    await choose("Copy into", "Archive");
    await choose("File handling", "Leave files in place");
    expect(screen.queryByRole("combobox", { name: "Copy into" })).toBeNull();

    const submit = screen.getByRole("button", { name: "Connect" }) as HTMLButtonElement;
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);
    await waitFor(() => expect(mocks.connect).toHaveBeenCalledExactlyOnceWith({
      backendClass: "remote_source", name: "Team", credentialId: "crd_1", root: "/", mode: "REFERENCE",
    }, { errorNotification: false }));
    await settle();
    // The refused connect keeps the dialog open.
    expect(screen.getByRole("dialog", { name: "remote.mount.connect.title" })).toBeTruthy();

    await choose("File handling", "Copy files");
    expect(screen.getByRole("combobox", { name: "Copy into" }).textContent).toBe(initialTarget);
  });
});

/** The app's providers the dialog renders under: the router and `ModalsHost` it asks through, and the toasts. */
function AppProviders({ children }: { children?: ReactNode }): ReactElement {
  const [router] = useState(() => createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) }));
  return <RouterContextProvider router={router}><ModalsHost><ToastProvider>{children}</ToastProvider></ModalsHost></RouterContextProvider>;
}

const settle = () => act(() => new Promise<void>((resolve) => { setTimeout(resolve, 0); }));
const useThisFolder = () => screen.getByRole("button", { name: "Use this folder" });

/** Open the dialog with a name, the first account and its root folder chosen. */
async function openWithFolder(): Promise<void> {
  render(
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <ConnectMountAction backendClass="remote_source" i18nPrefix="remote.mount.connect" />
    </AppRuntimeProvider>,
    { wrapper: AppProviders },
  );
  fireEvent.click(screen.getByRole("button", { name: "remote.mount.connect.button" }));
  fireEvent.change(await screen.findByRole("textbox", { name: "Name" }), { target: { value: "Team" } });
  fireEvent.click(screen.getByRole("button", { name: "ada@example.com" }));
  fireEvent.click(await screen.findByRole("button", { name: "Use this folder" }));
  await waitFor(() => expect(useThisFolder().getAttribute("aria-pressed")).toBe("true"));
}

async function choose(field: string, option: string): Promise<void> {
  fireEvent.click(screen.getByRole("combobox", { name: field }));
  const choice = await screen.findByRole("option", { name: option });
  fireEvent.pointerDown(choice, { pointerType: "mouse" });
  fireEvent.click(choice);
  await waitFor(() => expect(screen.getByRole("combobox", { name: field }).textContent).toContain(option));
}
