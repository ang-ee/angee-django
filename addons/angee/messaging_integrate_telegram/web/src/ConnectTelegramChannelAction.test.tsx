// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  connectMutation: vi.fn(async (_variables: unknown) => ({
    connect_telegram_channel: { id: "chn_1" },
  })),
  createKeysMutation: vi.fn(async () => ({
    create_credential: { id: "cred_9", display_name: "My keys" },
  })),
  mutationOptions: null as Record<string, unknown> | null,
  dialogProps: null as Record<string, unknown> | null,
  pairingDialogProps: null as Record<string, unknown> | null,
}));

// Documents stand in as opaque tokens so the mutation mock can tell the connect
// action from the inline app-keys create without depending on hook call order.
vi.mock("./documents", () => ({
  ConnectTelegramChannel: "ConnectTelegramChannel",
  CreateTelegramAppKeys: "CreateTelegramAppKeys",
}));

vi.mock("@angee/refine", () => ({
  useAuthoredMutation: (document: unknown, options?: Record<string, unknown>) => {
    expect(document).toBe("CreateTelegramAppKeys");
    expect(options).toBeUndefined();
    return [actionMocks.createKeysMutation];
  },
}));

vi.mock("@angee/messaging", () => ({
  usePairingConnect: (document: unknown, resultField: string, instruction: string) => {
    expect(document).toBe("ConnectTelegramChannel");
    actionMocks.mutationOptions = {
      invalidateModels: ["messaging.Channel"],
    };
    const [channelId, setChannelId] = React.useState<string | null>(null);
    const connect = async (variables: unknown) => {
      const data = await actionMocks.connectMutation(variables);
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
          const onSubmit = props.onSubmit as (values: Record<string, unknown>) => Promise<unknown>;
          void onSubmit({ name: "  Ada Telegram  ", credential: "cred_9" });
        }}
      >
        <button type="submit">{props.submitLabel as string}</button>
      </form>
    );
  },
}));

vi.mock("./i18n", () => ({
  useMessagingTelegramT: () => (key: string) => key,
}));

import { ConnectTelegramChannelAction } from "./ConnectTelegramChannelAction";

type DialogField = {
  name: string;
  relation?: {
    resource: string;
    filters?: readonly unknown[];
    create?: {
      fields?: readonly { name: string; description?: React.ReactNode }[];
      submit: (
        data: Record<string, unknown>,
        context: unknown,
      ) => Promise<unknown>;
    };
  };
};

function openDialogFields(): DialogField[] {
  render(<ConnectTelegramChannelAction />);
  fireEvent.click(screen.getByRole("button", { name: /channel.telegram.button/ }));
  return actionMocks.dialogProps?.fields as DialogField[];
}

function openCredentialField(): DialogField {
  const credential = openDialogFields().find((field) => field.name === "credential");
  if (!credential) throw new Error("The connect dialog declares no credential field.");
  return credential;
}

describe("ConnectTelegramChannelAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    actionMocks.connectMutation.mockClear();
    actionMocks.createKeysMutation.mockClear();
    actionMocks.mutationOptions = null;
    actionMocks.dialogProps = null;
    actionMocks.pairingDialogProps = null;
  });

  test("selects an app-keys credential and opens the shared pairing dialog", async () => {
    const fields = openDialogFields();

    expect(actionMocks.mutationOptions).toEqual({ invalidateModels: ["messaging.Channel"] });
    // The dialog asks for a name and a credential — never the keys themselves.
    expect(fields).toMatchObject([
      { name: "name" },
      {
        name: "credential",
        relation: {
          resource: "Credential",
          filters: [{ field: "kind", operator: "eq", value: "app_keys" }],
        },
      },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "channel.telegram.submit" }));

    await waitFor(() =>
      expect(actionMocks.connectMutation).toHaveBeenCalledWith({
        name: "Ada Telegram",
        credentialId: "cred_9",
      }),
    );
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(actionMocks.pairingDialogProps).toMatchObject({
      channelId: "chn_1",
      instruction: "channel.telegram.scan",
    });
  });

  test("creates app keys inline through the picker's own save owner", async () => {
    const create = openCredentialField().relation?.create;

    expect(create?.fields).toMatchObject([
      { name: "name" },
      { name: "app_id", kind: "integer" },
      { name: "app_secret", widget: "password" },
    ]);

    // The credential resource exposes no create root, so the picker saves through
    // this owner and selects the row it returns.
    const row = await create?.submit(
      { name: "  My keys  ", app_id: 123456, app_secret: "  telegram-api-hash  " },
      { resource: "Credential", id: null, isCreate: true, record: null, lines: null },
    );

    expect(actionMocks.createKeysMutation).toHaveBeenCalledWith({
      name: "My keys",
      appId: "123456",
      appSecret: "telegram-api-hash",
    });
    expect(row).toEqual({ id: "cred_9", display_name: "My keys" });
  });

  test("points the operator at their own Telegram application registration", () => {
    const secretField = openCredentialField().relation?.create?.fields?.find(
      (field) => field.name === "app_secret",
    );

    render(<>{secretField?.description}</>);

    expect(
      screen.getByRole("link", { name: "channel.telegram.keysLink" }).getAttribute("href"),
    ).toBe("https://my.telegram.org/");
  });
});
