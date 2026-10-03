// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { composeAddons } from "@angee/app";
import type { TypedDocumentNode } from "@angee/refine";
import type { SlotContribution } from "@angee/ui";
import * as React from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

const pageMocks = vi.hoisted(() => ({
  resourceProps: null as Record<string, unknown> | null,
  columnFields: [] as string[],
  fieldNames: [] as string[],
  recordAction: vi.fn(),
  requestedSlots: [] as string[],
  slotEntries: [] as readonly SlotContribution[],
  connect: vi.fn(async () => ({})),
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const original = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...original,
    registerForm: (resource: string, Component: React.ComponentType<Record<string, unknown>>) => ({ resource, Component }),
    Action: ({ label, run }: { label: string; run?: () => void }) => (
      <button type="button" onClick={() => run?.()}>
        {label}
      </button>
    ),
    Column: ({ field }: { field: string }) => {
      pageMocks.columnFields.push(field);
      return null;
    },
    Field: ({ name }: { name: string }) => {
      pageMocks.fieldNames.push(name);
      return null;
    },
    Form: ({ children }: { children?: React.ReactNode }) => <section>{children}</section>,
    Group: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
    List: ({ children }: { children?: React.ReactNode }) => <section>{children}</section>,
    ResourceList: (props: Record<string, unknown>) => {
      pageMocks.resourceProps = props;
      return (
        <div>
          {props.toolbarActions as React.ReactNode}
          {props.children as React.ReactNode}
          {props.form
            ? React.createElement(
                (props.form as { Component: React.ComponentType<{ resource: string }> }).Component,
                { resource: String(props.resource) },
              )
            : null}
        </div>
      );
    },
    useRecordActionMutation: () => [pageMocks.recordAction],
    useSlot: (slot: string) => {
      pageMocks.requestedSlots.push(slot);
      return original.useSlot(slot);
    },
  };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: () => [pageMocks.connect, { fetching: false }],
}));

import { AppRuntimeProvider, createAngeeI18nInstance, defaultWidgets, ToastProvider } from "@angee/ui";
import { ConnectChannelAction } from "./ConnectChannelAction";
import { ChannelsPage } from "./ChannelsPage";
import { MESSAGING_CHANNEL_FORM_FIELDS_SLOT, MESSAGING_CHANNEL_TOOLBAR_SLOT } from "./slots";

beforeAll(() => { Element.prototype.getAnimations ??= () => []; });

describe("ChannelsPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    pageMocks.resourceProps = null;
    pageMocks.columnFields = [];
    pageMocks.fieldNames = [];
    pageMocks.recordAction.mockClear();
    pageMocks.requestedSlots = [];
    pageMocks.connect.mockClear();
    pageMocks.slotEntries = [
      connectEntry("IMAP", 10),
      { slot: MESSAGING_CHANNEL_FORM_FIELDS_SLOT, id: "demo-fields", content: "Bridge form fields" },
    ];
  });

  test("renders a model-driven channels page with addon toolbar actions", () => {
    renderPage();

    expect(pageMocks.resourceProps).toMatchObject({
      resource: "messaging.Channel",
      placement: "inline",
      routed: true,
      hideCreate: true,
    });
    expect(pageMocks.requestedSlots).toEqual([
      MESSAGING_CHANNEL_TOOLBAR_SLOT,
      MESSAGING_CHANNEL_FORM_FIELDS_SLOT,
    ]);
    expect(screen.getAllByRole("button", { name: "Connect" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Connect IMAP" })).toBeNull();
    expect(screen.getByText("Bridge form fields")).toBeTruthy();
    expect(pageMocks.columnFields).toEqual(
      expect.arrayContaining(["sync_stage", "last_sync_items", "last_sync_completed_at"]),
    );
    expect(pageMocks.fieldNames).toEqual(
      expect.arrayContaining([
        "credential_status",
        "slug",
        "is_published",
        "form_schema_version",
        "form_schema",
        "max_body_bytes",
        "max_field_bytes",
        "is_syncing",
        "sync_stage",
        "sync_error",
        "sync_progress",
        "last_sync_summary",
      ]),
    );
  });

  test("omits Connect when no toolbar vendors contribute", () => {
    pageMocks.slotEntries = [];
    renderPage();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });

  test("omits Connect when toolbar entries contain no renderable content", () => {
    pageMocks.slotEntries = [
      { slot: MESSAGING_CHANNEL_TOOLBAR_SLOT, id: "empty", content: [null, false, undefined, []] },
    ];
    renderPage();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });

  test("lists every contributed vendor in sequence order and retains its dialog after closing the menu", async () => {
    // Deliberately scramble manifest order; composition owns slot ordering.
    pageMocks.slotEntries = composeAddons([
      { id: "vendor-discord", slots: [connectEntry("Discord", 25)] },
      { id: "vendor-imap", slots: [connectEntry("IMAP", 10)] },
      { id: "vendor-matrix", slots: [connectEntry("Matrix", 23)] },
      { id: "vendor-whatsapp", slots: [connectEntry("WhatsApp", 20)] },
      { id: "vendor-slack", slots: [connectEntry("Slack", 24)] },
      { id: "vendor-signal", slots: [connectEntry("Signal", 22)] },
      { id: "vendor-telegram", slots: [connectEntry("Telegram", 21)] },
    ], { canonicalModelLabel: (label) => label }).slots;
    renderPage();
    const trigger = screen.getByRole("button", { name: "Connect" });
    expect(screen.getAllByRole("button", { name: "Connect" })).toHaveLength(1);
    for (const vendor of vendors) {
      fireEvent.click(trigger);
      await screen.findByRole("menu");
      expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(
        vendors.map((name) => `Connect ${name}`),
      );
      fireEvent.click(screen.getByRole("menuitem", { name: `Connect ${vendor}` }));
      const dialog = await screen.findByRole("dialog", { name: `${vendor} connection` });
      await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
      expect(screen.getByRole("dialog", { name: `${vendor} connection` })).toBe(dialog);
      expect(screen.getByRole("menuitem", { name: `Connect ${vendor}`, hidden: true }).getAttribute("tabindex")).toBe("-1");
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(document.activeElement).toBe(trigger));
    }
    // Opening and dismissing these dialogs never runs a connect mutation.
    expect(pageMocks.connect).not.toHaveBeenCalled();
  });
});


const vendors = ["IMAP", "WhatsApp", "Telegram", "Signal", "Matrix", "Slack", "Discord"];
const connectDocument = {} as TypedDocumentNode<Record<string, unknown>, { name: string }>;
const fields = () => [{ name: "name", label: "Name", required: true }];
const parseValues = () => ({ name: "example" });

function connectEntry(vendor: string, sequence: number): SlotContribution {
  return {
    slot: MESSAGING_CHANNEL_TOOLBAR_SLOT,
    id: `connect-${vendor}`,
    sequence,
    content: <ConnectChannelAction kind="mutation" document={connectDocument} fields={fields}
      i18nPrefix={`channel.${vendor}`} parseValues={parseValues} />,
  };
}

function renderPage(): void {
  const messaging = Object.fromEntries(vendors.flatMap((vendor) => [
    [`channel.${vendor}.button`, `Connect ${vendor}`],
    [`channel.${vendor}.title`, `${vendor} connection`],
    [`channel.${vendor}.description`, `Connect an example ${vendor} account.`],
  ]));
  render(
    <AppRuntimeProvider runtime={{ slots: pageMocks.slotEntries, widgets: defaultWidgets,
      i18n: createAngeeI18nInstance({ messaging }) }}>
      <ToastProvider><ChannelsPage /></ToastProvider>
    </AppRuntimeProvider>,
  );
}
