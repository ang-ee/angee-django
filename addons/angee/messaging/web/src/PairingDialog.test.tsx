// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { RecordChromeContext } from "@angee/ui";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { PairingSnapshot } from "./PairingDialog";

const mocks = vi.hoisted(() => ({
  chrome: {
    resource: "messaging.Channel",
    dataProviderName: "console",
    canonicalResource: "integrate.Integration",
    recordId: "chn_1",
    record: { lifecycle: "DISCONNECTED" },
  } as RecordChromeContext,
  pairing: {
    state: "STARTING",
    qr: "",
    message: "",
    can_skip: false,
    account_label: "",
    duplicate_channel_name: "",
  } as PairingSnapshot,
  queryError: null as Error | null,
  queryVariables: null as Record<string, unknown> | null,
  queryOptions: null as { dataProviderName?: string; models?: readonly string[] } | null,
  // `useActionResultMutation` (the `@angee/ui` owner) maps invalidation, fires the
  // verb and settles its outcome; these tests assert what the dialog declares to
  // it, and its own package owns testing that ceremony.
  actions: new Map<string, ReturnType<typeof vi.fn>>(),
  actionOptions: new Map<string, { invalidateModels?: readonly string[] }>(),
  actionStates: new Map<string, { fetching: boolean; error: Error | null }>(),
  recordActions: new Map<string, ReturnType<typeof vi.fn>>(),
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: (
    _document: unknown,
    variables: Record<string, unknown>,
    options: { dataProviderName?: string; models?: readonly string[] },
  ) => {
    mocks.queryVariables = variables;
    mocks.queryOptions = options;
    return {
      data: mocks.queryError ? undefined : {
        channel_pairing: mocks.pairing,
      },
      fetching: false,
      error: mocks.queryError,
      refetch: () => undefined,
    };
  },
}));

vi.mock("@angee/ui", () => ({
  createNamespaceT:
    (_namespace: string, messages: Record<string, string>) => () =>
      (key: string) =>
        messages[key] ?? key,
  useActionResultMutation: (
    field: string,
    options?: { invalidateModels?: readonly string[] },
  ) => {
    const action = mocks.actions.get(field) ?? vi.fn(async () => undefined);
    mocks.actions.set(field, action);
    mocks.actionOptions.set(field, options ?? {});
    return [
      action,
      mocks.actionStates.get(field) ?? { fetching: false, error: null },
    ];
  },
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" {...props}>{children}</button>
  ),
  Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
  useRecordChromeContext: () => mocks.chrome,
  useRecordChromeActionMutation: (field: string) => {
    const action = mocks.recordActions.get(field) ?? vi.fn(async () => undefined);
    mocks.recordActions.set(field, action);
    return [action, { fetching: false, error: null }];
  },
  DialogRoot: ({ children, open }: { children: React.ReactNode; open?: boolean }) =>
    open ? <div role="dialog">{children}</div> : null,
  DialogPortal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DialogBackdrop: () => null,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogBody: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ErrorBanner: ({ description }: { description?: React.ReactNode }) => (
    <div role="alert">{description}</div>
  ),
  FieldRoot: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  FieldLabel: ({ children, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) => (
    <label {...props}>{children}</label>
  ),
  FieldControl: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}));

vi.mock("./i18n", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./i18n")>();
  return {
    ...actual,
    useMessagingT: () => (key: string) => {
      const message = actual.enMessagingMessages[key];
      if (message === undefined) throw new Error(`Missing messaging message: ${key}`);
      return message;
    },
  };
});

import { ChannelPairingAction, PairingDialog } from "./PairingDialog";

const lifecycleIs = (expected: string) =>
  ({ record }: RecordChromeContext & { record: NonNullable<RecordChromeContext["record"]> }) =>
    String(record.lifecycle ?? "").toLowerCase() === expected;

describe("PairingDialog", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.pairing = {
      state: "STARTING",
      qr: "",
      message: "",
      can_skip: false,
      account_label: "",
      duplicate_channel_name: "",
    };
    mocks.queryError = null;
    mocks.queryVariables = null;
    mocks.queryOptions = null;
    mocks.actions.clear();
    mocks.actionOptions.clear();
    mocks.actionStates.clear();
    mocks.chrome = {
      resource: "messaging.Channel",
      dataProviderName: "console",
      canonicalResource: "integrate.Integration",
      recordId: "chn_1",
      record: { lifecycle: "DISCONNECTED" },
    };
    mocks.recordActions.clear();
  });

  test("renders nothing without a channel to pair", () => {
    render(<PairingDialog channelId={null} instruction="Scan in the vendor app." onClose={() => undefined} />);

    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("names no schema, leaving the active data provider to resolve it", () => {
    // The dialog renders from a channel's record-verb slot *and* from the channel
    // list's toolbar; hardcoding `"console"` was wrong in both. Both hooks fall
    // back to the ambient provider, so the dialog declares neither.
    render(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);

    expect(mocks.queryOptions?.dataProviderName).toBeUndefined();
    expect(mocks.queryOptions?.models).toEqual(["messaging.Channel"]);
    expect(mocks.queryVariables).toEqual({ id: "chn_1" });
  });

  test("declares the channel model every pairing verb moves", () => {
    // `useActionMutation` defaults to no invalidation, so a verb that moves the
    // channel's lifecycle must name its target or the record goes stale.
    mocks.pairing.state = "LOGGED_OUT";
    render(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);

    for (const field of [
      "reset_channel_pairing",
      "resume_channel_pairing",
      "skip_channel_password",
      "submit_channel_password",
    ]) {
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
    render(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "Re-pair" }));

    await waitFor(() =>
      expect(mocks.actions.get("reset_channel_pairing")).toHaveBeenCalledWith("chn_1"),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("fires the resume verb through the shared record-chrome owner", async () => {
    mocks.pairing.state = "PAUSED";
    render(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "Resume" }));

    await waitFor(() =>
      expect(mocks.actions.get("resume_channel_pairing")).toHaveBeenCalledWith("chn_1"),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("offers repair for a duplicate account and resume for a stopped session", () => {
    // Re-pair cannot reconcile the same account — rescanning it hits the same
    // conflict — but it is the way to link a different account on this channel.
    mocks.pairing.state = "DUPLICATE_ACCOUNT";
    const { rerender } = render(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);
    expect(screen.getByRole("button", { name: "Re-pair" })).toBeTruthy();

    mocks.pairing.state = "STOPPED";
    rerender(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
  });

  test("reports every pairing state the generated union carries", () => {
    // The body map is keyed by the codegen `PairingState` union, so a member added
    // to the Python enum fails this file's typecheck. The ternary chain it replaced
    // fell through to "Starting…" instead, silently misreporting with a green
    // typecheck — which defeated the point of reading the generated union.
    for (const [state, copy] of [
      ["STARTING", "Starting the pairing session…"],
      ["AWAITING_SCAN", "Scan in the vendor app."],
      ["AWAITING_PASSWORD", "Account password"],
      ["PAIRED", "Linked! Messages are syncing. (Account One)"],
      ["PAUSED", "This channel connection is paused."],
      ["LOGGED_OUT", "This account unlinked the session."],
      ["STOPPED", "The pairing session stopped before completing."],
      [
        "DUPLICATE_ACCOUNT",
        "This account is already connected to another channel. (Existing channel)",
      ],
    ] as const) {
      cleanup();
      mocks.pairing = {
        state,
        qr: state === "AWAITING_SCAN" ? "data:image/png;base64,qr" : "",
        message: "",
        can_skip: false,
        account_label: state === "PAIRED" ? "Account One" : "",
        duplicate_channel_name:
          state === "DUPLICATE_ACCOUNT" ? "Existing channel" : "",
      };
      render(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);
      expect(screen.getByText(copy)).toBeTruthy();
    }
  });

  test("submits one password with the vendor message and clears local state", async () => {
    mocks.pairing = {
      ...mocks.pairing,
      state: "AWAITING_PASSWORD" as PairingSnapshot["state"],
      message: "Your account requires its cloud password.",
    };
    render(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);

    expect(screen.getByText("Your account requires its cloud password.")).toBeTruthy();
    const input = screen.getByLabelText("Account password") as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.autocomplete).toBe("current-password");

    fireEvent.change(input, { target: { value: "one-use-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit password" }));

    await waitFor(() =>
      expect(mocks.actions.get("submit_channel_password")).toHaveBeenCalledWith(
        "chn_1",
        { password: "one-use-secret" },
      ),
    );
    await waitFor(() => expect(input.value).toBe(""));
  });

  test("offers and submits an explicit skip only for optional password rounds", async () => {
    mocks.pairing = {
      ...mocks.pairing,
      state: "AWAITING_PASSWORD" as PairingSnapshot["state"],
      can_skip: true,
    };
    const { rerender } = render(
      <PairingDialog
        channelId="chn_1"
        instruction="Scan in the vendor app."
        onClose={() => undefined}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Skip" }));

    await waitFor(() =>
      expect(mocks.actions.get("skip_channel_password")).toHaveBeenCalledWith(
        "chn_1",
      ),
    );

    mocks.pairing.can_skip = false;
    rerender(
      <PairingDialog
        channelId="chn_1"
        instruction="Scan in the vendor app."
        onClose={() => undefined}
      />,
    );
    expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
  });

  test("reads as starting until the QR for an awaiting-scan session lands", () => {
    mocks.pairing = {
      state: "AWAITING_SCAN",
      qr: "",
      message: "",
      can_skip: false,
      account_label: "",
      duplicate_channel_name: "",
    };
    render(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);

    expect(screen.getByText(/Starting the pairing session/)).toBeTruthy();
  });

  test("renders the authored read error instead of an eternal starting state", () => {
    mocks.queryError = new Error("Pairing state could not be loaded.");

    render(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);

    expect(screen.getByRole("alert").textContent).toContain(
      "Pairing state could not be loaded.",
    );
    expect(screen.queryByText(/Starting the pairing session/)).toBeNull();
  });

  test("degrades an unknown rolling-deploy state to starting", () => {
    mocks.pairing = {
      ...mocks.pairing,
      state: "FUTURE_STATE" as PairingSnapshot["state"],
    };

    render(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);

    expect(screen.getByText(/Starting the pairing session/)).toBeTruthy();
  });

  test("keeps an in-flight footer action mounted and disabled", () => {
    mocks.pairing.state = "LOGGED_OUT";
    const { rerender } = render(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);
    const button = screen.getByRole("button", { name: "Re-pair" });
    button.focus();
    mocks.actionStates.set("reset_channel_pairing", {
      fetching: true,
      error: null,
    });
    mocks.pairing.state = "STARTING";

    rerender(<PairingDialog channelId="chn_1" instruction="Scan in the vendor app." onClose={() => undefined} />);

    expect(
      (screen.getByRole("button", { name: "Re-pair" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(document.activeElement).toBe(button);
  });
});

describe("ChannelPairingAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.pairing = {
      state: "STARTING",
      qr: "",
      message: "",
      can_skip: false,
      account_label: "",
      duplicate_channel_name: "",
    };
    mocks.chrome = {
      resource: "messaging.Channel",
      dataProviderName: "console",
      canonicalResource: "integrate.Integration",
      recordId: "chn_1",
      record: { lifecycle: "DISCONNECTED" },
    };
    mocks.recordActions.clear();
  });

  test("resumes a disconnected record and opens the shared dialog", async () => {
    render(
      <ChannelPairingAction
        labelKey="channel.pairing.connect"
        instructionKey="channel.pairing.qrAlt"
        resumeOnOpen
        when={lifecycleIs("disconnected")}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /Connect/ }),
    );

    await waitFor(() =>
      expect(mocks.recordActions.get("resume_channel_pairing")).toHaveBeenCalledWith(
        "chn_1",
      ),
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  test("resolves the vendor instruction through the messaging namespace", () => {
    mocks.pairing = {
      state: "AWAITING_SCAN",
      qr: "data:image/png;base64,qr",
      message: "",
      can_skip: false,
      account_label: "",
      duplicate_channel_name: "",
    };
    render(
      <ChannelPairingAction
        labelKey="channel.pairing.connect"
        instructionKey="channel.pairing.qrAlt"
        resumeOnOpen
        when={lifecycleIs("disconnected")}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /Connect/ }),
    );

    expect(screen.getByText("Channel pairing QR code")).toBeTruthy();
  });

  test("renders nothing before the record loads", () => {
    mocks.chrome.record = null;
    render(
      <ChannelPairingAction
        labelKey="channel.pairing.connect"
        instructionKey="channel.pairing.qrAlt"
        resumeOnOpen
        when={lifecycleIs("disconnected")}
      />,
    );

    expect(
      screen.queryByRole("button", { name: /Connect/ }),
    ).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("keeps an open dialog mounted after the lifecycle changes", async () => {
    const { rerender } = render(
      <ChannelPairingAction
        labelKey="channel.pairing.connect"
        instructionKey="channel.pairing.qrAlt"
        resumeOnOpen
        when={lifecycleIs("disconnected")}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Connect/ }),
    );
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());

    mocks.chrome.record = { lifecycle: "CONNECTED" };
    rerender(
      <ChannelPairingAction
        labelKey="channel.pairing.connect"
        instructionKey="channel.pairing.qrAlt"
        resumeOnOpen
        when={lifecycleIs("disconnected")}
      />,
    );

    expect(
      screen.queryByRole("button", { name: /Connect/ }),
    ).toBeNull();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  test("does not open or resume for a lifecycle owned by another entry", () => {
    mocks.chrome.record = { lifecycle: "CONNECTED" };
    render(
      <ChannelPairingAction
        labelKey="channel.pairing.connect"
        instructionKey="channel.pairing.qrAlt"
        resumeOnOpen
        when={lifecycleIs("disconnected")}
      />,
    );

    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mocks.recordActions.get("resume_channel_pairing")).not.toHaveBeenCalled();
  });

  test("resumes a paused channel", async () => {
    mocks.chrome.record = { lifecycle: "PAUSED" };
    render(
      <ChannelPairingAction
        labelKey="channel.pairing.resume"
        instructionKey="channel.pairing.qrAlt"
        resumeOnOpen
        when={lifecycleIs("paused")}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Resume/ }),
    );

    await waitFor(() =>
      expect(mocks.recordActions.get("resume_channel_pairing")).toHaveBeenCalledWith(
        "chn_1",
      ),
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  test("opens a connected channel without re-declaring intent", async () => {
    mocks.chrome.record = { lifecycle: "CONNECTED" };
    render(
      <ChannelPairingAction
        labelKey="channel.pairing.status"
        instructionKey="channel.pairing.qrAlt"
        when={lifecycleIs("connected")}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Pairing status/ }),
    );

    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(mocks.recordActions.get("resume_channel_pairing")).not.toHaveBeenCalled();
  });

  test("leaves a disconnected channel to the connect entry", () => {
    render(
      <ChannelPairingAction
        labelKey="channel.pairing.status"
        instructionKey="channel.pairing.qrAlt"
        when={lifecycleIs("connected")}
      />,
    );

    expect(screen.queryByRole("button")).toBeNull();
  });
});
