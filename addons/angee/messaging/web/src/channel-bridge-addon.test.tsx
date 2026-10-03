// @vitest-environment happy-dom

import { composeAddons, type AddonManifest } from "@angee/app";
import integrate, {
  INTEGRATION_DISCONNECT_ACTION_ID,
  INTEGRATION_MODEL,
  INTEGRATION_RESUME_ACTION_ID,
} from "@angee/integrate";
import { resolveContainer, type ComposedContainerChild } from "@angee/ui";
import { cleanup, render, screen } from "@testing-library/react";
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
import { expectValidChannelBridgeAddon } from "./testing";

const VENDOR = "messaging-integrate-example";

/**
 * The channel form's record verbs for a row of `impl`, composed as an app
 * would: integrate declares the lifecycle verbs on the MTI parent, the vendor
 * (which depends on it) adds and specializes them on `messaging.Channel`.
 */
function composed(manifest: AddonManifest) {
  return composeAddons([
    { id: integrate.id, containers: integrate.containers },
    // Messaging declares the Channels toolbar the vendors' connect verbs join.
    { id: "messaging", dependsOn: [integrate.id], containers: { "messaging.channels#toolbar": {} } },
    { id: manifest.id, dependsOn: [integrate.id, "messaging"], containers: manifest.containers },
  ], { canonicalModelLabel: (model) => model }).containers;
}

function channelVerbs(manifest: AddonManifest, impl: string) {
  const containers = composed(manifest);
  const options = { models: [INTEGRATION_MODEL, CHANNEL_MODEL], impls: [impl] };
  return {
    toolbar: resolveContainer<React.ReactNode>(containers, "form#actions", options),
    menu: resolveContainer<React.ReactNode>(containers, "form#actions-menu", options),
  };
}

/** The vendor's connect verbs on messaging's Channels toolbar (`messaging.channels#toolbar`). */
function channelToolbar(manifest: AddonManifest) {
  return summary(resolveContainer(composed(manifest), "messaging.channels#toolbar"));
}

const summary = (children: readonly ComposedContainerChild[]) =>
  children.map(({ id, sequence }) => ({ id, sequence }));

function childContent(children: readonly ComposedContainerChild<React.ReactNode>[], id: string): React.ReactNode {
  const child = children.find((candidate) => candidate.id === id);
  if (!child) throw new Error(`No child "${id}".`);
  return child.content;
}

function liveBridge(options: Partial<Parameters<typeof defineChannelBridgeAddon>[0]> = {}) {
  return defineChannelBridgeAddon({
    id: VENDOR,
    key: "example",
    sequence: 22,
    connectAction: <span>Connect example</span>,
    i18n: {
      messaging: {
        "channel.example.menu.label": "Example",
        "channel.example.menu.description": "Link Example accounts",
      },
    },
    ...options,
  });
}

describe("defineChannelBridgeAddon live bridges", () => {
  afterEach(() => {
    cleanup();
    mocks.disconnect.length = 0;
  });

  test("owns the complete impl-scoped channel bridge manifest", () => {
    const manifest = liveBridge({ instructionKey: "channel.example.scan" });
    expect(() => expectValidChannelBridgeAddon(manifest)).not.toThrow();

    expect(manifest.menus?.[0]).toMatchObject({
      id: "messaging.example",
      label: "Example",
      parentId: "messaging",
      route: "messaging.channels",
    });
    expect(channelToolbar(manifest)).toEqual([{ id: `${VENDOR}.connect`, sequence: 22 }]);

    // On the vendor's own rows: its pairing verbs join integrate's, and its
    // variants stand in for integrate's resume and disconnect.
    const own = channelVerbs(manifest, "example");
    expect(summary(own.toolbar)).toEqual([
      { id: `${VENDOR}.connect`, sequence: 10 },
      { id: `${VENDOR}.pairing`, sequence: 10 },
      { id: "integrate.lifecycle.pause", sequence: 11 },
      { id: `${VENDOR}.resume`, sequence: 12 },
    ]);
    expect(own.toolbar.find((child) => child.id === `${VENDOR}.resume`)?.variant)
      .toEqual({ of: INTEGRATION_RESUME_ACTION_ID, impl: "example" });
    expect(summary(own.menu)).toEqual([
      { id: `${VENDOR}.disconnect`, sequence: 13 },
      { id: "integrate.connection.test", sequence: 14 },
    ]);
    expect(own.menu[0]?.variant).toEqual({ of: INTEGRATION_DISCONNECT_ACTION_ID, impl: "example" });

    // Another backend's rows keep integrate's verbs untouched.
    const other = channelVerbs(manifest, "imap");
    expect(other.toolbar.map((child) => child.id)).toEqual(["integrate.lifecycle.pause", INTEGRATION_RESUME_ACTION_ID]);
    expect(other.menu.map((child) => child.id)).toEqual([INTEGRATION_DISCONNECT_ACTION_ID, "integrate.connection.test"]);

    render(childContent(own.menu, `${VENDOR}.disconnect`) as React.ReactElement);
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
    const manifest = liveBridge({ instructionKey: "channel.example.scan", disconnectAction: override });

    expect(childContent(channelVerbs(manifest, "example").menu, `${VENDOR}.disconnect`)).toBe(override);
  });

  test("does not require scan copy for a static-token live bridge", () => {
    const manifest = liveBridge();

    render(childContent(channelVerbs(manifest, "example").toolbar, `${VENDOR}.connect`) as React.ReactElement);

    expect(
      screen
        .getByText((_content, element) =>
          element?.getAttribute("data-pairing") === "channel.pairing.connect"
        )
        .hasAttribute("data-instruction"),
    ).toBe(false);
  });

  test("owns the pairing lifecycle matrix and resume-on-open policy", () => {
    const manifest = liveBridge();
    const { toolbar } = channelVerbs(manifest, "example");
    const pairing = [`${VENDOR}.connect`, `${VENDOR}.pairing`, `${VENDOR}.resume`].map((id) =>
      (childContent(toolbar, id) as React.ReactElement<{
        when: (context: { record: Record<string, unknown> }) => boolean;
        resumeOnOpen?: boolean;
      }>).props
    );
    const lifecycles = ["DISCONNECTED", "CONNECTED", "PAUSED"];

    expect(
      pairing.map((props) =>
        lifecycles.map((lifecycle) => props.when({ record: { lifecycle } }))
      ),
    ).toEqual([
      [true, false, false],
      [false, true, false],
      [false, false, true],
    ]);
    expect(pairing.map((props) => props.resumeOnOpen ?? false)).toEqual([
      true,
      false,
      true,
    ]);
  });
});

describe("defineChannelPollBridgeAddon poll bridges", () => {
  const pollBridge = (recordActions?: Parameters<typeof defineChannelPollBridgeAddon>[0]["recordActions"]) =>
    defineChannelPollBridgeAddon({
      id: VENDOR,
      key: "example",
      sequence: 22,
      connectAction: <span>Connect example</span>,
      i18n: {
        messaging: {
          "channel.example.menu.label": "Example",
          "channel.example.menu.description": "Sync Example accounts",
        },
      },
      ...(recordActions ? { recordActions } : {}),
    });

  test("scopes vendor record verbs to the vendor's own channel rows", () => {
    const manifest = pollBridge([
      { id: `${VENDOR}.credential`, sequence: 20, content: <span>Update credential</span> },
    ]);
    expect(() => expectValidChannelBridgeAddon(manifest)).not.toThrow();

    const own = channelVerbs(manifest, "example").menu;
    expect(own.find((child) => child.id === `${VENDOR}.credential`)).toMatchObject({ impl: "example", sequence: 20 });
    expect(own.map((child) => child.id)).toEqual([
      INTEGRATION_DISCONNECT_ACTION_ID,
      "integrate.connection.test",
      `${VENDOR}.credential`,
    ]);
    expect(channelVerbs(manifest, "imap").menu.map((child) => child.id)).not.toContain(`${VENDOR}.credential`);
  });

  test("keeps poll bridges free of live pairing record verbs", () => {
    const manifest = pollBridge();
    expect(() => expectValidChannelBridgeAddon(manifest)).not.toThrow();

    expect(manifest.menus?.[0]).toMatchObject({
      id: "messaging.example",
      label: "Example",
      route: "messaging.channels",
      description: "Sync Example accounts",
    });
    expect(channelToolbar(manifest)).toEqual([{ id: `${VENDOR}.connect`, sequence: 22 }]);
    // Integrate's lifecycle verbs reach the vendor's rows unspecialized.
    const { toolbar, menu } = channelVerbs(manifest, "example");
    expect([...toolbar, ...menu].map((child) => child.owner)).toEqual(["integrate", "integrate", "integrate", "integrate"]);
  });

  test("fails fast when required vendor menu copy is missing", () => {
    expect(() =>
      defineChannelPollBridgeAddon({
        id: VENDOR,
        key: "example",
        sequence: 22,
        connectAction: <span>Connect example</span>,
        i18n: { messaging: { "channel.example.menu.label": "Example" } },
      }),
    ).toThrowError(
      "Channel bridge example is missing i18n message channel.example.menu.description.",
    );
  });
});
