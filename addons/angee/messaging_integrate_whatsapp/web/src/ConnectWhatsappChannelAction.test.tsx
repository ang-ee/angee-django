// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  authoredMutation: vi.fn(async (_variables: unknown) => ({
    connect_whatsapp_channel: { id: "chn_1" },
  })),
  mutationOptions: null as Record<string, unknown> | null,
  dialogProps: null as Record<string, unknown> | null,
  pairingDialogProps: null as Record<string, unknown> | null,
  settled: vi.fn(async (fire: () => Promise<unknown>) => fire()),
}));

vi.mock("@angee/messaging", () => ({
  usePairingConnect: (_document: unknown, resultField: string, instruction: string) => {
    actionMocks.mutationOptions = {
      invalidateModels: ["messaging.Channel"],
    };
    const [channelId, setChannelId] = React.useState<string | null>(null);
    const connect = async (variables: unknown) => {
      const data = await actionMocks.authoredMutation(variables);
      const result = data[resultField as keyof typeof data];
      if (result?.id) setChannelId(String(result.id));
      return data;
    };
    const props = {
      channelId,
      instruction,
      onClose: () => setChannelId(null),
    };
    actionMocks.pairingDialogProps = props;
    return {
      connect,
      pairingDialog: channelId ? <div role="dialog">{instruction}</div> : null,
    };
  },
}));

vi.mock("@angee/ui", () => ({
  useActionResultRun: () => actionMocks.settled,
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
    actionMocks.pairingDialogProps = null;
    actionMocks.settled.mockClear();
  });

  test("connects with a trimmed name, then opens the live pairing pane", async () => {
    render(<ConnectWhatsappChannelAction />);

    expect(actionMocks.mutationOptions).toEqual({ invalidateModels: ["messaging.Channel"] });
    fireEvent.click(screen.getByRole("button", { name: /channel.whatsapp.button/ }));
    fireEvent.click(screen.getByRole("button", { name: "channel.whatsapp.submit" }));

    await waitFor(() =>
      expect(actionMocks.authoredMutation).toHaveBeenCalledWith({ name: "Personal WhatsApp" }),
    );
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(actionMocks.pairingDialogProps).toMatchObject({
      channelId: "chn_1",
      instruction: "channel.whatsapp.scan",
    });
  });
});
