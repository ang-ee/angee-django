// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  chrome: {
    resource: "messaging.Channel",
    canonicalResource: "integrate.Integration",
    dataProviderName: "console",
    recordId: "chn_1",
    record: { backend_class: "WHATSAPP", lifecycle: "DISCONNECTED" },
  } as {
    resource: string;
    canonicalResource: string;
    dataProviderName: string;
    recordId: string;
    record: Record<string, unknown> | null;
  },
  pairing: {
    state: "STARTING",
    qr: "",
    phone: "",
    duplicate_channel_name: "",
  },
  queryOptions: null as { dataProviderName?: string } | null,
  // One fake per action field, standing in for the `@angee/ui` owner that maps
  // invalidation, fires the verb, and settles its outcome — so these tests assert
  // what the addon declares, not the ceremony it no longer spells.
  actions: new Map<string, ReturnType<typeof vi.fn>>(),
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: (
    _document: unknown,
    _variables: unknown,
    options: { dataProviderName?: string },
  ) => {
    mocks.queryOptions = options;
    return {
      data: { whatsapp_pairing: mocks.pairing },
      fetching: false,
      error: null,
      refetch: () => undefined,
    };
  },
}));

// A stub of integrate's enum-casing read, not the real one: this file stubs the
// whole `@angee/ui`/`@angee/refine` surface it renders against, and integrate's
// index composes far more of both than those stubs carry. What the stub must not
// become is a copy of a *rule* — the lifecycle sets `WhatsappDisconnectAction`
// gates on are integrate's `isConnectedOrPaused`, and that test imports the real
// one. This only lower-cases, so a record fixture reads as the row does.
vi.mock("@angee/integrate", () => ({
  integrationLifecycle: (record: Record<string, unknown>) =>
    String(record.lifecycle ?? "").toLowerCase(),
}));

const actionMock = (field: string): unknown[] => {
  const action = mocks.actions.get(field) ?? vi.fn(async () => undefined);
  mocks.actions.set(field, action);
  return [action, { fetching: false, error: null }];
};

vi.mock("@angee/ui", () => ({
  useRecordChromeContext: () => mocks.chrome,
  useRecordChromeActionMutation: (field: string) => actionMock(field),
  // The dialog this verb opens also renders from the channel-list toolbar, so it
  // binds to no record context and takes the plain owner.
  useActionResultMutation: (field: string) => actionMock(field),
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" {...props}>{children}</button>
  ),
  Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
  DialogRoot: ({ children, open }: { children: React.ReactNode; open?: boolean }) =>
    open ? <div role="dialog">{children}</div> : null,
  DialogPortal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DialogBackdrop: () => null,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogBody: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("./i18n", () => ({
  useMessagingWhatsappT: () => (key: string) => key,
}));

import { WhatsappConnectionAction } from "./WhatsappConnectionAction";

describe("WhatsappConnectionAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.chrome = {
      resource: "messaging.Channel",
      canonicalResource: "integrate.Integration",
      dataProviderName: "console",
      recordId: "chn_1",
      record: { backend_class: "WHATSAPP", lifecycle: "DISCONNECTED" },
    };
    mocks.pairing = {
      state: "STARTING",
      qr: "",
      phone: "",
      duplicate_channel_name: "",
    };
    mocks.queryOptions = null;
    mocks.actions.clear();
  });

  test("resumes a disconnected record and opens the shared dialog", async () => {
    render(<WhatsappConnectionAction lifecycle="disconnected" />);

    fireEvent.click(screen.getByRole("button", { name: /channel.whatsapp.connect/ }));

    await waitFor(() =>
      expect(mocks.actions.get("resume_whatsapp_pairing")).toHaveBeenCalledWith("chn_1"),
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  test("renders no button before the record loads", () => {
    // The impl slot key resolves off the record, so this entry can be mounted for
    // a beat with no record — it must gate rather than assume a lifecycle.
    mocks.chrome.record = null;
    render(<WhatsappConnectionAction lifecycle="disconnected" />);

    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("hides the button once the record connects but keeps an open dialog mounted", async () => {
    // `resume_whatsapp_pairing` sets the channel CONNECTED on click, so the
    // record's live push re-renders this with the button's gate already false.
    // Gating the dialog on lifecycle too would unmount the QR pane at exactly the
    // moment it has something to report. Only the button is gated.
    const { rerender } = render(<WhatsappConnectionAction lifecycle="disconnected" />);
    fireEvent.click(screen.getByRole("button", { name: /channel.whatsapp.connect/ }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());

    mocks.chrome.record = { backend_class: "WHATSAPP", lifecycle: "CONNECTED" };
    mocks.pairing.state = "PAIRED";
    rerender(<WhatsappConnectionAction lifecycle="disconnected" />);

    expect(
      screen.queryByRole("button", { name: /channel.whatsapp.connect/ }),
    ).toBeNull();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByText(/channel.whatsapp.paired/)).toBeTruthy();
  });

  test("shows no button for a connected record that never opened the dialog", () => {
    mocks.chrome.record = { backend_class: "WHATSAPP", lifecycle: "CONNECTED" };
    mocks.pairing.state = "PAIRED";
    render(<WhatsappConnectionAction lifecycle="disconnected" />);

    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    // The mutation hook binds on every render (it must — hooks are unconditional);
    // what must not happen is the verb firing.
    expect(mocks.actions.get("resume_whatsapp_pairing")).not.toHaveBeenCalled();
  });

  test("resumes a paused account directly from the blue Resume button", async () => {
    mocks.chrome.record = { backend_class: "WHATSAPP", lifecycle: "PAUSED" };
    mocks.pairing.state = "PAUSED";
    render(<WhatsappConnectionAction lifecycle="paused" />);
    fireEvent.click(screen.getByRole("button", { name: /channel.whatsapp.resume/ }));

    await waitFor(() =>
      expect(mocks.actions.get("resume_whatsapp_pairing")).toHaveBeenCalledWith("chn_1"),
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  test("opens the dialog on a connected channel without re-declaring the intent", async () => {
    // The gap this guards: a logged-out channel keeps its CONNECTED lifecycle, so
    // the two entries above never reach it and `resetWhatsappPairing` — the repair
    // `ensure_sessions` waits for — had no console path. Firing resume here would
    // redispatch the session that just failed, which is what that task's
    // `runtime_status` gate exists to prevent, so this entry only reads.
    mocks.chrome.record = { backend_class: "WHATSAPP", lifecycle: "CONNECTED" };
    mocks.pairing.state = "LOGGED_OUT";
    render(<WhatsappConnectionAction lifecycle="connected" />);

    fireEvent.click(screen.getByRole("button", { name: /channel.whatsapp.pairing/ }));

    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(screen.getByText(/channel.whatsapp.loggedOut/)).toBeTruthy();
    expect(mocks.actions.get("resume_whatsapp_pairing")).not.toHaveBeenCalled();
  });

  test("leaves a disconnected channel to the Connect entry", () => {
    // Each entry gates on exactly one lifecycle, so the cluster never shows both.
    render(<WhatsappConnectionAction lifecycle="connected" />);

    expect(screen.queryByRole("button")).toBeNull();
  });

  test("explains a duplicate account through the dialog it opens", () => {
    mocks.pairing.state = "DUPLICATE_ACCOUNT";
    mocks.pairing.duplicate_channel_name = "Personal WhatsApp";
    render(<WhatsappConnectionAction lifecycle="disconnected" />);
    fireEvent.click(screen.getByRole("button", { name: /channel.whatsapp.connect/ }));

    expect(screen.getByText(/channel.whatsapp.duplicate/).textContent).toContain(
      "Personal WhatsApp",
    );
  });
});
