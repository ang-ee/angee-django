// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  authoredMutation: vi.fn(async () => ({ connect_slack_channel: { id: "chn_1" } })),
  mutationOptions: null as Record<string, unknown> | null,
  dialogProps: null as Record<string, unknown> | null,
}));

vi.mock("./documents", () => ({
  ConnectSlackChannel: "ConnectSlackChannel",
}));

vi.mock("@angee/refine", () => ({
  useAuthoredMutation: (_document: unknown, options: Record<string, unknown>) => {
    actionMocks.mutationOptions = options;
    return [actionMocks.authoredMutation];
  },
}));

vi.mock("@angee/messaging", () => ({
  CHANNEL_MODEL: "messaging.Channel",
}));

vi.mock("@angee/ui", () => ({
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
          void onSubmit({ name: "Acme", token: " xoxp-user-token " });
        }}
      >
        <button type="submit">{props.submitLabel as string}</button>
      </form>
    );
  },
  createNamespaceT: () => () => (key: string) => key,
}));

vi.mock("./i18n", () => ({
  useMessagingSlackT: () => (key: string) => key,
}));

import { ConnectSlackChannelAction } from "./ConnectSlackChannelAction";

describe("ConnectSlackChannelAction", () => {
  beforeEach(() => {
    actionMocks.authoredMutation.mockClear();
    actionMocks.mutationOptions = null;
    actionMocks.dialogProps = null;
  });

  test("opens the app-token dialog and submits normalized variables", async () => {
    render(<ConnectSlackChannelAction />);

    expect(actionMocks.mutationOptions).toEqual({ invalidateModels: ["messaging.Channel"] });
    fireEvent.click(screen.getByRole("button", { name: /channel.slack.button/ }));

    const fields = actionMocks.dialogProps?.fields as readonly { name: string; description?: React.ReactNode }[];
    expect(fields.map((field) => field.name)).toEqual(["name", "token"]);
    render(<>{fields[1]?.description}</>);
    expect(screen.getByRole("link", { name: "channel.slack.appsLink" }).getAttribute("href")).toBe(
      "https://api.slack.com/apps",
    );

    fireEvent.click(screen.getByRole("button", { name: "channel.slack.submit" }));
    await waitFor(() =>
      expect(actionMocks.authoredMutation).toHaveBeenCalledWith({
        name: "Acme",
        token: "xoxp-user-token",
      }),
    );
  });
});
