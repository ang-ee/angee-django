// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  authoredMutation: vi.fn(async () => ({ connect_signal_channel: { id: "chn_1" } })),
  mutationOptions: null as Record<string, unknown> | null,
  pairingDialogProps: null as Record<string, unknown> | null,
  danger: vi.fn(),
}));

vi.mock("@angee/refine", () => ({
  useAuthoredMutation: (_document: unknown, options: Record<string, unknown>) => {
    actionMocks.mutationOptions = options;
    return [actionMocks.authoredMutation, { fetching: false, error: null }];
  },
}));

vi.mock("@angee/messaging", () => ({
  CHANNEL_MODEL: "messaging.Channel",
  PairingDialog: (props: Record<string, unknown>) => {
    actionMocks.pairingDialogProps = props;
    return props.channelId ? <div role="dialog">{props.instruction as string}</div> : null;
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
