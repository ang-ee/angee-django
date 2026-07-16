// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  authoredMutation: vi.fn(async () => ({ connect_telegram_channel: { id: "chn_1" } })),
  mutationOptions: null as Record<string, unknown> | null,
  dialogProps: null as Record<string, unknown> | null,
  pairingDialogProps: null as Record<string, unknown> | null,
}));

vi.mock("@angee/refine", () => ({
  useAuthoredMutation: (_document: unknown, options: Record<string, unknown>) => {
    actionMocks.mutationOptions = options;
    return [actionMocks.authoredMutation];
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
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" {...props}>{children}</button>
  ),
  Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
  MutationDialog: (props: Record<string, unknown>) => {
    actionMocks.dialogProps = props;
    if (!props.open) return null;
    const fields = props.fields as {
      name: string;
      kind?: string;
      widget?: string;
      description?: React.ReactNode;
    }[];
    return (
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const onSubmit = props.onSubmit as (values: Record<string, unknown>) => Promise<unknown>;
          void onSubmit({
            name: "  Ada Telegram  ",
            api_id: 123456,
            api_hash: "  telegram-api-hash  ",
          });
        }}
      >
        {fields.map((field) => <div key={field.name}>{field.description}</div>)}
        <button type="submit">{props.submitLabel as string}</button>
      </form>
    );
  },
}));

vi.mock("./i18n", () => ({
  useMessagingTelegramT: () => (key: string) => key,
}));

import { ConnectTelegramChannelAction } from "./ConnectTelegramChannelAction";

describe("ConnectTelegramChannelAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    actionMocks.authoredMutation.mockClear();
    actionMocks.mutationOptions = null;
    actionMocks.dialogProps = null;
    actionMocks.pairingDialogProps = null;
  });

  test("submits Telegram application keys and opens the shared pairing dialog", async () => {
    render(<ConnectTelegramChannelAction />);

    expect(actionMocks.mutationOptions).toEqual({ invalidateModels: ["messaging.Channel"] });
    fireEvent.click(screen.getByRole("button", { name: /channel.telegram.button/ }));
    expect(actionMocks.dialogProps?.fields).toMatchObject([
      { name: "name" },
      { name: "api_id", kind: "integer" },
      { name: "api_hash", widget: "password" },
    ]);
    expect(
      screen.getByRole("link", { name: "channel.telegram.keysLink" }).getAttribute("href"),
    ).toBe("https://my.telegram.org/");
    fireEvent.click(screen.getByRole("button", { name: "channel.telegram.submit" }));

    await waitFor(() => expect(actionMocks.authoredMutation).toHaveBeenCalledWith({
      name: "Ada Telegram",
      apiId: "123456",
      apiHash: "telegram-api-hash",
    }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(actionMocks.pairingDialogProps).toMatchObject({
      channelId: "chn_1",
      instruction: "channel.telegram.scan",
    });
  });
});
