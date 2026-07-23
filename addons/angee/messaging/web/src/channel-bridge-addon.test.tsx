// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import {
  INTEGRATION_DISCONNECT_ACTION_ID,
  INTEGRATION_RESUME_ACTION_ID,
} from "@angee/integrate";
import { formViewRecordActionsSlot } from "@angee/ui";
import * as React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  disconnect: [] as Record<string, unknown>[],
}));

vi.mock("@angee/integrate", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/integrate")>()),
  ConditionalMutationButton: (props: Record<string, unknown>) => {
    mocks.disconnect.push(props);
    return <button type="button">{props.label as React.ReactNode}</button>;
  },
}));

vi.mock("./i18n", () => ({
  useMessagingT: () => (key: string) => key,
}));

vi.mock("./PairingDialog", () => ({
  ChannelPairingAction: (props: Record<string, unknown>) => (
    <span
      data-pairing={String(props.labelKey)}
      {...(props.instructionKey
        ? { "data-instruction": String(props.instructionKey) }
        : {})}
    />
  ),
}));

import {
  defineChannelBridgeAddon,
  defineChannelPollBridgeAddon,
} from "./channel-bridge-addon";
import { CHANNEL_MODEL } from "./documents";
import { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "./slots";

describe("defineChannelBridgeAddon live bridges", () => {
  afterEach(() => {
    cleanup();
    mocks.disconnect.length = 0;
  });

  test("owns the complete impl-scoped channel bridge manifest", () => {
    const manifest = defineChannelBridgeAddon({
      id: "messaging-integrate-example",
      key: "example",
      sequence: 22,
      connectAction: <span>Connect example</span>,
      i18n: {
        "channel.example.menu.label": "Example",
        "channel.example.menu.description": "Link Example accounts",
      },
      instructionKey: "channel.example.scan",
    });
    const recordSlot = formViewRecordActionsSlot(CHANNEL_MODEL, "example");

    expect(manifest.menus?.[0]).toMatchObject({
      id: "messaging.example",
      label: "Example",
      parentId: "messaging",
      to: "/messaging/channels",
    });
    expect(manifest.slots?.map(({ slot, id, sequence }) => ({ slot, id, sequence }))).toEqual([
      {
        slot: MESSAGING_CHANNEL_TOOLBAR_SLOT,
        id: "messaging-integrate-example.connect",
        sequence: 22,
      },
      {
        slot: recordSlot,
        id: "messaging-integrate-example.connect",
        sequence: 10,
      },
      {
        slot: recordSlot,
        id: "messaging-integrate-example.pairing",
        sequence: 10,
      },
      { slot: recordSlot, id: INTEGRATION_RESUME_ACTION_ID, sequence: 12 },
      { slot: recordSlot, id: INTEGRATION_DISCONNECT_ACTION_ID, sequence: 13 },
    ]);

    render(manifest.slots?.[4]?.content as React.ReactElement);
    expect(screen.getByRole("button", { name: "channel.pairing.disconnect" })).toBeTruthy();
    expect(mocks.disconnect[0]).toMatchObject({
      field: "disconnect_channel",
      variant: "danger",
      confirm: {
        title: "channel.pairing.disconnectConfirm.title",
        body: "channel.pairing.disconnectConfirm.body",
        danger: true,
      },
    });
  });

  test("accepts a vendor specialization of the disconnect action", () => {
    const override = <span>Custom disconnect</span>;
    const manifest = defineChannelBridgeAddon({
      id: "messaging-integrate-example",
      key: "example",
      sequence: 22,
      connectAction: <span>Connect example</span>,
      i18n: {
        "channel.example.menu.label": "Example",
        "channel.example.menu.description": "Link Example accounts",
      },
      instructionKey: "channel.example.scan",
      disconnectAction: override,
    });

    expect(manifest.slots?.[4]?.content).toBe(override);
  });

  test("does not require scan copy for a static-token live bridge", () => {
    const manifest = defineChannelBridgeAddon({
      id: "messaging-integrate-example",
      key: "example",
      sequence: 22,
      connectAction: <span>Connect example</span>,
      i18n: {
        "channel.example.menu.label": "Example",
        "channel.example.menu.description": "Link Example accounts",
      },
    });

    render(manifest.slots?.[1]?.content as React.ReactElement);

    expect(
      screen
        .getByText((_content, element) =>
          element?.getAttribute("data-pairing") === "channel.pairing.connect"
        )
        .hasAttribute("data-instruction"),
    ).toBe(false);
  });
});

describe("defineChannelPollBridgeAddon poll bridges", () => {
  test("keeps poll bridges free of live pairing record verbs", () => {
    const manifest = defineChannelPollBridgeAddon({
      id: "messaging-integrate-example",
      key: "example",
      sequence: 22,
      connectAction: <span>Connect example</span>,
      i18n: {
        "channel.example.menu.label": "Example",
        "channel.example.menu.description": "Sync Example accounts",
      },
    });

    expect(manifest.menus?.[0]).toMatchObject({
      id: "messaging.example",
      label: "Example",
      description: "Sync Example accounts",
    });
    expect(manifest.slots?.map(({ slot, id, sequence }) => ({ slot, id, sequence }))).toEqual([
      {
        slot: MESSAGING_CHANNEL_TOOLBAR_SLOT,
        id: "messaging-integrate-example.connect",
        sequence: 22,
      },
    ]);
  });

  test("fails fast when required vendor menu copy is missing", () => {
    expect(() =>
      defineChannelPollBridgeAddon({
        id: "messaging-integrate-example",
        key: "example",
        sequence: 22,
        connectAction: <span>Connect example</span>,
        i18n: { "channel.example.menu.label": "Example" },
      }),
    ).toThrowError(
      "Channel bridge example is missing i18n message channel.example.menu.description.",
    );
  });
});
