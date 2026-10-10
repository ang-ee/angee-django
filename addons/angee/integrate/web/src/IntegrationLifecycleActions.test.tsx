// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  chrome: {
    resource: "messaging.Channel",
    canonicalResource: "integrate.Integration",
    dataProviderName: "console",
    recordId: "int_1",
    record: null as Record<string, unknown> | null,
  },
}));

vi.mock("@angee/ui", async () => {
  const actual = await vi.importActual<typeof import("@angee/ui")>("@angee/ui");
  return {
    ...actual,
    ActionFormDialog: () => null,
    Button: ({ children }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
      <button type="button">{children}</button>
    ),
    Glyph: () => null,
    useConfirm: () => vi.fn(async () => true),
    useRecordChromeContext: () => mocks.chrome,
    useRecordChromeActionMutation: () => [vi.fn(), { fetching: false, error: null }],
    useRecordChromeActionOutcome: () => [vi.fn(), { fetching: false, error: null }],
  };
});

import { ResumeIntegrationAction, RetryBindingAction, integrationHasCredential } from "./IntegrationLifecycleActions";

describe("ResumeIntegrationAction", () => {
  afterEach(cleanup);
  beforeEach(() => {
    mocks.chrome.record = null;
  });

  test("reaches a subtype row through the model's projected admission", () => {
    mocks.chrome.record = { can_resume: true };
    render(<ResumeIntegrationAction />);
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
  });

  test("stays hidden on a disconnected row that holds no credential", () => {
    mocks.chrome.record = { can_resume: false };
    render(<ResumeIntegrationAction />);
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
  });

  test("requires OAuth reconnect instead of resuming revoked material", () => {
    mocks.chrome.record = { can_resume: false, lifecycle: "PAUSED", credential_status: "revoked", is_reconnect_required: true };
    render(<ResumeIntegrationAction />);
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
  });

  test("reads only the shared health projection", () => {
    expect(integrationHasCredential({ credential: { id: "crd_1" } })).toBe(false);
    expect(integrationHasCredential({ credential_status: "active" })).toBe(true);
    expect(integrationHasCredential({ credential: null })).toBe(false);
    expect(integrationHasCredential({})).toBe(false);
  });

  test("offers discovery recovery only when its owner admits it", () => {
    mocks.chrome.record = { can_retry_binding: true };
    const { rerender } = render(<RetryBindingAction />);
    expect(screen.getByRole("button", { name: "Retry discovery" })).toBeTruthy();
    mocks.chrome.record = { can_retry_binding: false };
    rerender(<RetryBindingAction />);
    expect(screen.queryByRole("button")).toBeNull();
    mocks.chrome.record = { lifecycle: "PAUSED", credential_status: "active", binding_ready: false, binding_pending: false };
    rerender(<RetryBindingAction />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
