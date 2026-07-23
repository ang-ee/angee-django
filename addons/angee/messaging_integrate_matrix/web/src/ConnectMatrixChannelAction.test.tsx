// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  connect: vi.fn(async (_variables: unknown) => ({
    connect_matrix_channel: { id: "chn_1" },
  })),
  connectOptions: null as Record<string, unknown> | null,
  dialogProps: null as Record<string, unknown> | null,
  pairingDialogProps: null as Record<string, unknown> | null,
}));

vi.mock("./documents", () => ({
  ConnectMatrixChannel: "ConnectMatrixChannel",
}));

vi.mock("@angee/messaging", () => ({
  usePairingConnect: (document: unknown, resultField: string, instruction: string) => {
    expect(document).toBe("ConnectMatrixChannel");
    actionMocks.connectOptions = {
      invalidateModels: ["messaging.Channel"],
    };
    const [channelId, setChannelId] = React.useState<string | null>(null);
    const connect = async (variables: unknown) => {
      const data = await actionMocks.connect(variables);
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
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" {...props}>{children}</button>
  ),
  Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
  MutationDialog: (props: Record<string, unknown>) => {
    actionMocks.dialogProps = props;
    if (!props.open) return null;
    return (
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const submit = props.onSubmit as (values: Record<string, unknown>) => Promise<unknown>;
          void submit({
            homeserver: "  https://matrix.example.com/  ",
            username: "  @ada:example.com  ",
            password: "matrix-password",
          });
        }}
      >
        <button type="submit">{props.submitLabel as string}</button>
      </form>
    );
  },
}));

vi.mock("./i18n", () => ({
  useMessagingMatrixT: () => (key: string) => key,
}));

import { ConnectMatrixChannelAction } from "./ConnectMatrixChannelAction";

describe("ConnectMatrixChannelAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    actionMocks.connect.mockClear();
    actionMocks.connectOptions = null;
    actionMocks.dialogProps = null;
    actionMocks.pairingDialogProps = null;
  });

  test("atomically connects the channel, then opens recovery pairing", async () => {
    render(<ConnectMatrixChannelAction />);
    fireEvent.click(screen.getByRole("button", { name: /channel.matrix.button/ }));

    expect(actionMocks.dialogProps?.fields).toMatchObject([
      { name: "homeserver", required: true },
      { name: "username", required: true },
      { name: "password", widget: "password", required: true },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "channel.matrix.submit" }));

    await waitFor(() => expect(actionMocks.connect).toHaveBeenCalledWith({
      homeserver: "https://matrix.example.com/",
      username: "@ada:example.com",
      password: "matrix-password",
    }));
    expect(actionMocks.connectOptions).toEqual({ invalidateModels: ["messaging.Channel"] });
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(actionMocks.pairingDialogProps).toMatchObject({
      channelId: "chn_1",
      instruction: "channel.matrix.recovery",
    });
  });
});
