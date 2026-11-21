// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ModalsHost, ToastProvider, type FormProps, type ResourceListProps } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";

const mocks = vi.hoisted(() => ({
  rotate: vi.fn(async () => ({ rotate_webhook_secret: { ok: true, secret: "new-secret" } })),
  reveal: vi.fn(async () => undefined),
}));

vi.mock("@angee/refine", async (original) => ({
  ...await original<typeof import("@angee/refine")>(),
  useAuthoredMutation: () => [mocks.rotate, { fetching: false, error: null }],
}));

// Render the page's actual declarations through the native action and confirmation owners.
vi.mock("@angee/ui", async (original) => {
  const ui = await original<typeof import("@angee/ui")>();
  return {
    ...ui,
    ResourceList: ({ children }: ResourceListProps) => {
      const form = React.Children.toArray(children).find((child): child is React.ReactElement<FormProps> =>
        React.isValidElement<FormProps>(child) && child.type === ui.Form);
      return <ui.RecordActionBar record={{ id: "webhook-1" }} fillBand actions={ui.parsePageActions(form?.props.children)} />;
    },
    useRecordActionMutation: () => [vi.fn(), { fetching: false, error: null }],
    useSecretReveal: () => mocks.reveal,
  };
});

import { WebhooksPage } from "./WebhooksPage";

const { Provider, clearClients } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); mocks.rotate.mockClear(); mocks.reveal.mockClear(); });

test("rotation stays in the menu and changes the signing secret only after confirmation", async () => {
  render(<Provider><ModalsHost><ToastProvider><WebhooksPage /></ToastProvider></ModalsHost></Provider>);
  expect(screen.getByRole("button", { name: "Send test event" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Rotate secret" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Actions" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Rotate secret" }));
  const first = await screen.findByRole("alertdialog", { name: "Rotate secret" });
  expect(first.textContent).toContain("Update your receivers with the new secret");
  expect(mocks.rotate).not.toHaveBeenCalled();
  fireEvent.click(within(first).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(mocks.rotate).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Actions" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Rotate secret" }));
  const confirmation = await screen.findByRole("alertdialog", { name: "Rotate secret" });
  fireEvent.click(within(confirmation).getByRole("button", { name: "Rotate secret" }));
  await waitFor(() => expect(mocks.rotate).toHaveBeenCalledExactlyOnceWith({ id: "webhook-1" }));
  await waitFor(() => expect(mocks.reveal).toHaveBeenCalledWith(expect.objectContaining({
    secrets: [{ name: "secret", label: "Signing secret", value: "new-secret" }],
  })));
});
