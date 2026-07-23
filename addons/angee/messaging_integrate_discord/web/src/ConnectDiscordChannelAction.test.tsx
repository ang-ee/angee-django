// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  connect: vi.fn(async (_variables: unknown) => ({
    connect_discord_channel: { id: "chn_1" },
  })),
  dialogProps: null as Record<string, unknown> | null,
  submitValues: {
    name: "  Community bot  ",
    application_id: "123456789012345678",
    token: " bot-token ",
  } as Record<string, unknown>,
}));

vi.mock("./documents", () => ({
  ConnectDiscordChannel: "ConnectDiscordChannel",
}));

vi.mock("@angee/messaging", () => ({
  usePairingConnect: (document: unknown, resultField: string, instruction?: string) => {
    expect(document).toBe("ConnectDiscordChannel");
    expect(instruction).toBeUndefined();
    const [pairing, setPairing] = React.useState<{
      channelId: string;
      nextStep?: React.ReactNode;
    } | null>(null);
    const connect = async (
      variables: unknown,
      nextStep?: () => React.ReactNode,
    ) => {
      const data = await actionMocks.connect(variables);
      const result = data[resultField as keyof typeof data];
      if (result?.id) {
        setPairing({
          channelId: String(result.id),
          ...(nextStep ? { nextStep: nextStep() } : {}),
        });
      }
      return data;
    };
    return {
      connect,
      pairingDialog: pairing ? (
        <div role="dialog">
          {pairing.nextStep}
          <button type="button" onClick={() => setPairing(null)}>close</button>
        </div>
      ) : null,
    };
  },
}));

vi.mock("@angee/ui", () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" {...props}>{children}</button>
  ),
  Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
  MutationDialog: (props: Record<string, unknown>) => {
    const [error, setError] = React.useState("");
    actionMocks.dialogProps = props;
    if (!props.open) return null;
    return (
      <>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const submit = props.onSubmit as (
              values: Record<string, unknown>,
            ) => Promise<unknown>;
            void submit(actionMocks.submitValues).catch((cause: unknown) =>
              setError(cause instanceof Error ? cause.message : String(cause)),
            );
          }}
        >
          <button type="submit">{props.submitLabel as string}</button>
        </form>
        {error ? <div role="alert">{error}</div> : null}
      </>
    );
  },
}));

vi.mock("./i18n", () => ({
  useMessagingDiscordT: () => (key: string) => key,
}));

import {
  ConnectDiscordChannelAction,
  discordBotInviteUrl,
} from "./ConnectDiscordChannelAction";

describe("ConnectDiscordChannelAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    actionMocks.connect.mockReset();
    actionMocks.connect.mockResolvedValue({
      connect_discord_channel: { id: "chn_1" },
    });
    actionMocks.dialogProps = null;
    actionMocks.submitValues = {
      name: "  Community bot  ",
      application_id: "123456789012345678",
      token: " bot-token ",
    };
  });

  test("creates the bot channel, renders its invite, and opens pairing", async () => {
    render(<ConnectDiscordChannelAction />);
    fireEvent.click(screen.getByRole("button", { name: /channel.discord.button/ }));

    expect(
      (actionMocks.dialogProps?.fields as readonly { name: string }[]).map((field) => field.name),
    ).toEqual(["name", "application_id", "token"]);
    fireEvent.click(screen.getByRole("button", { name: "channel.discord.submit" }));

    await waitFor(() => expect(actionMocks.connect).toHaveBeenCalledWith({
      name: "Community bot",
      token: "bot-token",
    }));
    expect(screen.getByRole("link", { name: "channel.discord.invite" }).getAttribute("href")).toBe(
      "https://discord.com/oauth2/authorize?client_id=123456789012345678&scope=bot&permissions=66560",
    );
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "close" }));
    expect(screen.queryByRole("link", { name: "channel.discord.invite" })).toBeNull();
  });

  test("rejects a malformed application snowflake before connecting", async () => {
    actionMocks.submitValues.application_id = "not-a-snowflake";
    render(<ConnectDiscordChannelAction />);
    fireEvent.click(screen.getByRole("button", { name: /channel.discord.button/ }));
    fireEvent.click(screen.getByRole("button", { name: "channel.discord.submit" }));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "channel.discord.applicationIdInvalid",
      ),
    );
    expect(actionMocks.connect).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: "channel.discord.invite" })).toBeNull();
    expect(() => discordBotInviteUrl("123")).toThrow(/17–20 digit snowflake/);
  });

  test("does not surface an invite when channel creation fails", async () => {
    actionMocks.connect.mockRejectedValueOnce(new Error("Discord rejected the token."));
    render(<ConnectDiscordChannelAction />);
    fireEvent.click(screen.getByRole("button", { name: /channel.discord.button/ }));
    fireEvent.click(screen.getByRole("button", { name: "channel.discord.submit" }));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "Discord rejected the token.",
      ),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("link", { name: "channel.discord.invite" })).toBeNull();
  });
});
