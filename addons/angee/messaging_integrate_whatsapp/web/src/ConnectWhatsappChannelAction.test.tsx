// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  authoredMutation: vi.fn(async () => ({ connect_whatsapp_channel: { id: "chn_1" } })),
  mutationOptions: null as Record<string, unknown> | null,
  dialogProps: null as Record<string, unknown> | null,
  queryArgs: null as { variables: unknown; options: unknown } | null,
  pairing: { state: "STARTING", qr: "", jid: "", phone: "" } as Record<string, string>,
  actions: new Map<string, ReturnType<typeof vi.fn>>(),
  settled: vi.fn(async (fire: () => Promise<unknown>) => fire()),
}));

vi.mock("@angee/refine", () => ({
  useAuthoredMutation: (_document: unknown, options: Record<string, unknown>) => {
    actionMocks.mutationOptions = options;
    return [actionMocks.authoredMutation];
  },
  useAuthoredQuery: (_document: unknown, variables: unknown, options: unknown) => {
    actionMocks.queryArgs = { variables, options };
    return {
      data: { whatsapp_pairing: actionMocks.pairing },
      fetching: false,
      error: null,
      refetch: () => undefined,
    };
  },
}));

vi.mock("@angee/ui", () => ({
  useActionResultRun: () => actionMocks.settled,
  // The pairing pane this action opens fires its lifecycle verbs through the
  // shared `@angee/ui` owner, which maps invalidation and settles the outcome.
  useActionResultMutation: (field: string) => {
    const action = actionMocks.actions.get(field) ?? vi.fn(async () => undefined);
    actionMocks.actions.set(field, action);
    return [action, { fetching: false, error: null }];
  },
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
  Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
  MutationDialog: (props: Record<string, unknown>) => {
    actionMocks.dialogProps = props;
    if (!props.open) return null;
    return (
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const onSubmit = props.onSubmit as (values: Record<string, unknown>) => Promise<unknown>;
          void onSubmit({ name: "  Personal WhatsApp  " });
        }}
      >
        <button type="submit">{props.submitLabel as string}</button>
      </form>
    );
  },
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

import { ConnectWhatsappChannelAction } from "./ConnectWhatsappChannelAction";

describe("ConnectWhatsappChannelAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    actionMocks.authoredMutation.mockClear();
    actionMocks.mutationOptions = null;
    actionMocks.dialogProps = null;
    actionMocks.queryArgs = null;
    actionMocks.pairing = { state: "STARTING", qr: "", jid: "", phone: "" };
    actionMocks.actions.clear();
    actionMocks.settled.mockClear();
  });

  test("connects with a trimmed name, then opens the live pairing pane", async () => {
    actionMocks.pairing = {
      state: "AWAITING_SCAN",
      qr: "data:image/png;base64,QR",
      jid: "",
      phone: "",
    };
    render(<ConnectWhatsappChannelAction />);

    expect(actionMocks.mutationOptions).toEqual({ invalidateModels: ["messaging.Channel"] });
    fireEvent.click(screen.getByRole("button", { name: /channel.whatsapp.button/ }));
    fireEvent.click(screen.getByRole("button", { name: "channel.whatsapp.submit" }));

    await waitFor(() =>
      expect(actionMocks.authoredMutation).toHaveBeenCalledWith({ name: "Personal WhatsApp" }),
    );
    // The pairing pane is an authored read registered on the channel live
    // bridge — channelChanged refetches it; the component never polls.
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(actionMocks.queryArgs?.options).toMatchObject({ models: ["messaging.Channel"] });
    const qr = screen.getByAltText("channel.whatsapp.qrAlt");
    expect(qr.getAttribute("src")).toBe("data:image/png;base64,QR");
  });

  test("shows the paired state with the linked phone", async () => {
    actionMocks.pairing = {
      state: "PAIRED",
      qr: "",
      jid: "4917000001@s.whatsapp.net",
      phone: "+4917000001",
    };
    render(<ConnectWhatsappChannelAction />);
    fireEvent.click(screen.getByRole("button", { name: /channel.whatsapp.button/ }));
    fireEvent.click(screen.getByRole("button", { name: "channel.whatsapp.submit" }));

    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(screen.getByText(/channel.whatsapp.paired/).textContent).toContain("+4917000001");
  });
});
