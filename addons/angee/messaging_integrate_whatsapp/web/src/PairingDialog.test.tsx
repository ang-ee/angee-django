// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  pairing: {
    state: "STARTING",
    qr: "",
    phone: "",
    duplicate_channel_name: "",
  } as Record<string, string>,
  queryOptions: null as { dataProviderName?: string; models?: readonly string[] } | null,
  // `useActionResultMutation` (the `@angee/ui` owner) maps invalidation, fires the
  // verb and settles its outcome; these tests assert what the dialog declares to
  // it, and its own package owns testing that ceremony.
  actions: new Map<string, ReturnType<typeof vi.fn>>(),
  actionOptions: new Map<string, { invalidateModels?: readonly string[] }>(),
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: (
    _document: unknown,
    _variables: unknown,
    options: { dataProviderName?: string; models?: readonly string[] },
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

vi.mock("@angee/ui", () => ({
  useActionResultMutation: (
    field: string,
    options?: { invalidateModels?: readonly string[] },
  ) => {
    const action = mocks.actions.get(field) ?? vi.fn(async () => undefined);
    mocks.actions.set(field, action);
    mocks.actionOptions.set(field, options ?? {});
    return [action, { fetching: false, error: null }];
  },
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" {...props}>{children}</button>
  ),
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

import { PairingDialog } from "./PairingDialog";

describe("PairingDialog", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.pairing = {
      state: "STARTING",
      qr: "",
      phone: "",
      duplicate_channel_name: "",
    };
    mocks.queryOptions = null;
    mocks.actions.clear();
    mocks.actionOptions.clear();
  });

  test("renders nothing without a channel to pair", () => {
    render(<PairingDialog channelId={null} onClose={() => undefined} />);

    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("names no schema, leaving the active data provider to resolve it", () => {
    // The dialog renders from a channel's record-verb slot *and* from the channel
    // list's toolbar; hardcoding `"console"` was wrong in both. Both hooks fall
    // back to the ambient provider, so the dialog declares neither.
    render(<PairingDialog channelId="chn_1" onClose={() => undefined} />);

    expect(mocks.queryOptions?.dataProviderName).toBeUndefined();
    expect(mocks.queryOptions?.models).toEqual(["messaging.Channel"]);
  });

  test("declares the channel model both lifecycle verbs move", () => {
    // `useActionMutation` defaults to no invalidation, so a verb that moves the
    // channel's lifecycle must name its target or the record goes stale.
    mocks.pairing.state = "LOGGED_OUT";
    render(<PairingDialog channelId="chn_1" onClose={() => undefined} />);

    for (const field of ["reset_whatsapp_pairing", "resume_whatsapp_pairing"]) {
      expect(mocks.actionOptions.get(field)?.invalidateModels).toEqual([
        "messaging.Channel",
      ]);
    }
  });

  test("fires the repair verb through the shared record-chrome owner", async () => {
    // A domain refusal resolves `{ok:false}` rather than throwing, so a bare
    // `.catch()` would show nothing. The owner reads the outcome and toasts the
    // server's own message — the dialog renders no failure banner.
    mocks.pairing.state = "LOGGED_OUT";
    render(<PairingDialog channelId="chn_1" onClose={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "channel.whatsapp.repair" }));

    await waitFor(() =>
      expect(mocks.actions.get("reset_whatsapp_pairing")).toHaveBeenCalledWith("chn_1"),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("fires the resume verb through the shared record-chrome owner", async () => {
    mocks.pairing.state = "PAUSED";
    render(<PairingDialog channelId="chn_1" onClose={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "channel.whatsapp.resume" }));

    await waitFor(() =>
      expect(mocks.actions.get("resume_whatsapp_pairing")).toHaveBeenCalledWith("chn_1"),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("offers repair for a duplicate account and resume for a stopped session", () => {
    // Re-pair cannot reconcile the *same* phone — rescanning it hits the same
    // conflict — but it is the way to link a different account on this channel.
    mocks.pairing.state = "DUPLICATE_ACCOUNT";
    const { rerender } = render(<PairingDialog channelId="chn_1" onClose={() => undefined} />);
    expect(screen.getByRole("button", { name: "channel.whatsapp.repair" })).toBeTruthy();

    mocks.pairing.state = "STOPPED";
    rerender(<PairingDialog channelId="chn_1" onClose={() => undefined} />);
    expect(screen.getByRole("button", { name: "channel.whatsapp.resume" })).toBeTruthy();
  });

  test("reports every pairing state the generated union carries", () => {
    // The body map is keyed by the codegen `PairingState` union, so a member added
    // to the Python enum fails this file's typecheck. The ternary chain it replaced
    // fell through to "Starting…" instead, silently misreporting with a green
    // typecheck — which defeated the point of reading the generated union.
    for (const [state, copy] of [
      ["STARTING", "channel.whatsapp.starting"],
      ["AWAITING_SCAN", "channel.whatsapp.scan"],
      ["PAIRED", "channel.whatsapp.paired"],
      ["PAUSED", "channel.whatsapp.paused"],
      ["LOGGED_OUT", "channel.whatsapp.loggedOut"],
      ["STOPPED", "channel.whatsapp.stopped"],
      ["DUPLICATE_ACCOUNT", "channel.whatsapp.duplicate"],
    ] as const) {
      cleanup();
      mocks.pairing = {
        state,
        qr: state === "AWAITING_SCAN" ? "data:image/png;base64,qr" : "",
        phone: "",
        duplicate_channel_name: "",
      };
      render(<PairingDialog channelId="chn_1" onClose={() => undefined} />);
      expect(screen.getByText(new RegExp(copy))).toBeTruthy();
    }
  });

  test("reads as starting until the QR for an awaiting-scan session lands", () => {
    mocks.pairing = { state: "AWAITING_SCAN", qr: "", phone: "", duplicate_channel_name: "" };
    render(<PairingDialog channelId="chn_1" onClose={() => undefined} />);

    expect(screen.getByText(/channel.whatsapp.starting/)).toBeTruthy();
  });
});
