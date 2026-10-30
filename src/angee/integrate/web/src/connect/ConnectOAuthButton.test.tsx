// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ConnectOAuthButton, type OAuthConnectPayload } from "./ConnectOAuthButton";

const mocks = vi.hoisted(() => ({ complete: vi.fn(), prompt: vi.fn(), success: vi.fn(), danger: vi.fn() }));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: () => [mocks.complete, { fetching: false, error: null }],
}));

vi.mock("@angee/metadata", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/metadata")>()),
  useResourceInvalidates: () => [],
  useSchemaFieldMetadata: () => ({ resources: [] }),
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  Button: ({ children, onClick, loading, disabled }: React.ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) => (
    <button type="button" onClick={onClick} disabled={disabled || loading}>{children}</button>
  ),
  Glyph: () => null,
  usePrompt: () => mocks.prompt,
  useToast: () => ({ success: mocks.success, danger: mocks.danger }),
}));

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

test("reconnects the projected row by its public id and reports an attached result", async () => {
  const start = vi.fn<(input: { id: string; redirectUri: string; next: string }) => Promise<OAuthConnectPayload>>().mockResolvedValue({
    attached: true, authorize_url: "", error: null, mode: "redirect", state: "", redirect_uri: "",
  });
  const connected = vi.fn();
  render(<ConnectOAuthButton
    row={{ id: "int_expired", can_connect: true, is_reconnect_required: true }}
    next="/feeds" start={start} onConnected={connected}
  />);
  fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  await waitFor(() => expect(connected).toHaveBeenCalledOnce());
  expect(start).toHaveBeenCalledWith({
    id: "int_expired", redirectUri: `${window.location.origin}/integrate/oauth/callback`, next: "/feeds",
  });
  expect(mocks.success).toHaveBeenCalledOnce();
  expect(mocks.complete).not.toHaveBeenCalled();
});

test("hides the action after credential health changes while preserving its hooks", () => {
  const start = vi.fn<(input: { id: string; redirectUri: string; next: string }) => Promise<OAuthConnectPayload>>();
  const props = { next: "/feeds", start, onConnected: vi.fn() };
  const { rerender } = render(<ConnectOAuthButton
    {...props} row={{ id: "int_1", can_connect: true }}
  />);
  expect(screen.getByRole("button", { name: "Connect" })).toBeTruthy();
  rerender(<ConnectOAuthButton
    {...props} row={{ id: "int_1", can_connect: false }}
  />);
  expect(screen.queryByRole("button")).toBeNull();
  rerender(<ConnectOAuthButton {...props} row={{ id: "int_1", is_oauth_connectable: true, credential_status: "" }} />);
  expect(screen.queryByRole("button")).toBeNull();
});

test("does not report success when manual completion has no result", async () => {
  const start = vi.fn<(input: { id: string; redirectUri: string; next: string }) => Promise<OAuthConnectPayload>>().mockResolvedValue({
    attached: false, authorize_url: "https://provider.example/authorize", error: null,
    mode: "manual", state: "expected", redirect_uri: "https://provider.example/callback",
  });
  mocks.prompt.mockResolvedValue({ pasted: "code#expected" });
  mocks.complete.mockResolvedValue(undefined);
  const connected = vi.fn();
  render(<ConnectOAuthButton
    row={{ id: "int_1", can_connect: true }}
    next="/feeds" start={start} onConnected={connected}
  />);
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  await waitFor(() => expect(mocks.danger).toHaveBeenCalledOnce());
  expect(connected).not.toHaveBeenCalled();
  expect(mocks.success).not.toHaveBeenCalled();
});


test("keeps caller-owned labels and selection without a row", async () => {
  const start = vi.fn().mockResolvedValue({ attached: true });
  const connected = vi.fn();
  render(<ConnectOAuthButton next="/inference" start={start} onConnected={connected}
    label="Connect provider" connectedTitle="Provider connected" startErrorTitle="Provider unavailable" />);
  fireEvent.click(screen.getByRole("button", { name: "Connect provider" }));
  await waitFor(() => expect(connected).toHaveBeenCalledOnce());
  expect(start).toHaveBeenCalledWith({
    redirectUri: `${window.location.origin}/integrate/oauth/callback`, next: "/inference",
  });
  expect(mocks.success).toHaveBeenCalledWith({ title: "Provider connected" });
});
