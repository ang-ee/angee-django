// @vitest-environment happy-dom

import { fireEvent, render, screen } from "@testing-library/react";
import * as React from "react";
import { describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({ navigate: vi.fn(), capability: "" }));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: () => ({ data: { integration_capabilities: [
    { resource: "agents.InferenceProvider", label: "Inference", icon: null, create_mode: "FORM" },
    { resource: "messaging.Channel", label: "Channel", icon: null, create_mode: "CONNECT" },
  ] }, isLoading: false, error: null }),
}));
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => (options: { search?: (previous: Record<string, unknown>) => Record<string, unknown> } | { to: string }) => {
    if ("search" in options && options.search) {
      mocks.capability = String(options.search({}).capability ?? "");
    } else mocks.navigate(options);
  },
  useSearch: () => ({ capability: mocks.capability }),
}));
vi.mock("@angee/ui", () => ({
  Button: ({ children, onClick, disabled }: { children: React.ReactNode; onClick?: () => void; disabled?: boolean }) => <button disabled={disabled} onClick={onClick}>{children}</button>,
  EmptyState: ({ title, actions }: { title: React.ReactNode; actions: React.ReactNode }) => <section><h1>{title}</h1>{actions}</section>,
  ErrorBanner: () => null,
  errorMessage: () => "error",
  Glyph: () => null,
  LoadingPanel: () => null,
  RegisteredFormView: ({ resource }: { resource: string }) => <div data-testid="registered-form">{resource}</div>,
  useResourceRecordHrefLookup: () => () => undefined,
  useResourceRoute: (resource: string) => resource === "messaging.Channel" ? "/messages/channels" : undefined,
  useRouteHref: () => () => "/integrate/add",
}));
vi.mock("../i18n", () => ({ useIntegrateT: () => (key: string) => key }));

import { AddIntegrationPage } from "./AddIntegrationPage";

describe("AddIntegrationPage", () => {
  test("renders the complete registered form for a FORM capability", () => {
    mocks.capability = "agents.InferenceProvider";
    render(<AddIntegrationPage />);
    expect(screen.getByTestId("registered-form").textContent).toBe("agents.InferenceProvider");
  });

  test("routes a CONNECT capability to its native collection", () => {
    mocks.capability = "messaging.Channel";
    render(<AddIntegrationPage />);
    fireEvent.click(screen.getByRole("button", { name: "integrations.add.continue" }));
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/messages/channels" });
  });
});
