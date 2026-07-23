// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  authoredMutation: vi.fn(async (_variables: unknown) => ({
    connect_signal_channel: { id: "chn_1" },
  })),
  mutationOptions: null as Record<string, unknown> | null,
  pairingDialogProps: null as Record<string, unknown> | null,
  danger: vi.fn(),
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
      connectState: { fetching: false, error: null },
      pairingDialog: channelId ? <div role="dialog">{instruction}</div> : null,
    };
  },
}));

vi.mock("@angee/ui", () => ({
  useToast: () => ({ danger: actionMocks.danger }),
  errorMessage: (_error: unknown, fallback: string) => fallback,
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
  Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
}));

vi.mock("./i18n", () => ({
  useMessagingSignalT: () => (key: string) => key,
}));

import { ConnectSignalChannelAction } from "./ConnectSignalChannelAction";

describe("ConnectSignalChannelAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    actionMocks.authoredMutation.mockClear();
    actionMocks.mutationOptions = null;
    actionMocks.pairingDialogProps = null;
    actionMocks.danger.mockClear();
  });

  test("creates a credential-free channel, then opens shared pairing", async () => {
    render(<ConnectSignalChannelAction />);

    expect(actionMocks.mutationOptions).toEqual({
      invalidateModels: ["messaging.Channel"],
    });
    fireEvent.click(screen.getByRole("button", { name: /channel.signal.button/ }));

    await waitFor(() => expect(actionMocks.authoredMutation).toHaveBeenCalledWith({}));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(actionMocks.pairingDialogProps).toMatchObject({
      channelId: "chn_1",
      instruction: "channel.signal.scan",
    });
  });
});
