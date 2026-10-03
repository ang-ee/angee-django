// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { composeAddons } from "@angee/app";
import type { TypedDocumentNode } from "@angee/refine";
import type { ComposedContainers, ContainerChild } from "@angee/ui";
import * as React from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

const pageMocks = vi.hoisted(() => ({
  resourceProps: null as Record<string, unknown> | null,
  columnFields: [] as string[],
  fieldNames: [] as string[],
  recordAction: vi.fn(),
  requestedContainers: [] as string[],
  containers: undefined as ComposedContainers | undefined,
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
    useContainer: (address: string, options?: Parameters<typeof original.useContainer>[1]) => {
      pageMocks.requestedContainers.push(address);
      return original.useContainer(address, options);
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

beforeAll(() => { Element.prototype.getAnimations ??= () => []; });

describe("ChannelsPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    pageMocks.resourceProps = null;
    pageMocks.columnFields = [];
    pageMocks.fieldNames = [];
    pageMocks.recordAction.mockClear();
    pageMocks.requestedContainers = [];
    pageMocks.connect.mockClear();
    pageMocks.containers = composeToolbar({ "vendor-imap": connectEntry("IMAP", 10) });
  });

  test("renders a model-driven channels page with addon toolbar actions", () => {
    renderPage();

    expect(pageMocks.resourceProps).toMatchObject({
      resource: "messaging.Channel",
      placement: "inline",
      routed: true,
      hideCreate: true,
    });
    expect(pageMocks.requestedContainers).toContain("messaging.channels#toolbar");
    expect(screen.getAllByRole("button", { name: "Connect" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Connect IMAP" })).toBeNull();
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
    pageMocks.containers = composeToolbar({});
    renderPage();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });

  test("omits Connect when toolbar entries contain no renderable content", () => {
    pageMocks.containers = composeToolbar({ "vendor-empty": { content: [null, false, undefined, []] } });
    renderPage();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });

  test("lists every contributed vendor in sequence order and retains its dialog after closing the menu", async () => {
    // Deliberately scramble manifest order; the container owns the ordering.
    pageMocks.containers = composeToolbar({
      "vendor-discord": connectEntry("Discord", 25),
      "vendor-imap": connectEntry("IMAP", 10),
      "vendor-matrix": connectEntry("Matrix", 23),
      "vendor-whatsapp": connectEntry("WhatsApp", 20),
      "vendor-slack": connectEntry("Slack", 24),
      "vendor-signal": connectEntry("Signal", 22),
      "vendor-telegram": connectEntry("Telegram", 21),
    });
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

/** Messaging's Connect menu with one connect child per vendor addon, composed as an app composes them. */
function composeToolbar(children: Readonly<Record<string, ContainerChild>>): ComposedContainers {
  return composeAddons([
    { id: "messaging", containers: { "messaging.channels#toolbar": {} } },
    ...Object.entries(children).map(([vendor, child]) => ({
      id: vendor,
      dependsOn: ["messaging"],
      containers: { "messaging.channels#toolbar": { [`${vendor}.connect`]: child } },
    })),
  ], { canonicalModelLabel: (label) => label }).containers;
}

function connectEntry(vendor: string, sequence: number): ContainerChild {
  return {
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
    <AppRuntimeProvider runtime={{ ...(pageMocks.containers ? { containers: pageMocks.containers } : {}), widgets: defaultWidgets,
      i18n: createAngeeI18nInstance({ messaging }) }}>
      <ToastProvider><ChannelsPage /></ToastProvider>
    </AppRuntimeProvider>,
  );
}
