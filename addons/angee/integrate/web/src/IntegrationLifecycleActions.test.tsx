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

import { ResumeIntegrationAction, integrationHasCredential } from "./IntegrationLifecycleActions";

describe("ResumeIntegrationAction", () => {
  afterEach(cleanup);
  beforeEach(() => {
    mocks.chrome.record = null;
  });

  test("reaches a disconnected subtype row through its projected credential status", () => {
    mocks.chrome.record = { lifecycle: "DISCONNECTED", credential_status: "active" };
    render(<ResumeIntegrationAction />);
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
  });

  test("stays hidden on a disconnected row that holds no credential", () => {
    mocks.chrome.record = { lifecycle: "DISCONNECTED", credential_status: "" };
    render(<ResumeIntegrationAction />);
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
  });

  test("reads either the parent relation or the subtype scalar", () => {
    expect(integrationHasCredential({ credential: { id: "crd_1" } })).toBe(true);
    expect(integrationHasCredential({ credential_status: "active" })).toBe(true);
    expect(integrationHasCredential({ credential: null })).toBe(false);
    expect(integrationHasCredential({})).toBe(false);
  });
});
