// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { beforeEach, expect, test, vi } from "vitest";

import { ConnectAccountButton } from "./ConnectAccountButton";

const mocks = vi.hoisted(() => ({ mutate: vi.fn(), prompt: vi.fn(), success: vi.fn(), danger: vi.fn() }));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: () => [mocks.mutate, { fetching: false, error: null }],
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

beforeEach(() => vi.clearAllMocks());

test("starts an account connect at its client for the consumer's scopes and returns to next", async () => {
  mocks.mutate.mockResolvedValue({ connect_account_start: { authorize_url: "", error: "refused", mode: "auto" } });
  render(<ConnectAccountButton oauthClientId="ocl_google" scopes={["drive.readonly"]} next="/mounts"
    label="Connect another account" />);
  fireEvent.click(screen.getByRole("button", { name: "Connect another account" }));
  await waitFor(() => expect(mocks.danger).toHaveBeenCalledOnce());
  expect(mocks.mutate).toHaveBeenCalledWith({
    id: "ocl_google",
    redirectUri: `${window.location.origin}/integrate/oauth/callback`,
    next: "/mounts",
    scopes: ["drive.readonly"],
  });
});
