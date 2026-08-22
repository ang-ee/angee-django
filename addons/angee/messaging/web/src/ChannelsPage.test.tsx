// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const pageMocks = vi.hoisted(() => ({
  resourceProps: null as Record<string, unknown> | null,
  columnFields: [] as string[],
  fieldNames: [] as string[],
  recordAction: vi.fn(),
  requestedSlot: "",
  slotEntries: [{ slot: "messaging.channel.toolbar", id: "demo", content: "Connect bridge" }],
}));

vi.mock("@angee/ui", () => ({
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
  SlotOutlet: ({ entries }: { entries: readonly { id: string; content: unknown }[] }) => {
    return <div>{entries.map((entry) => <span key={entry.id}>{String(entry.content)}</span>)}</div>;
  },
  ResourceList: (props: Record<string, unknown>) => {
    pageMocks.resourceProps = props;
    return (
      <div>
        {props.toolbarActions as React.ReactNode}
        {props.children as React.ReactNode}
      </div>
    );
  },
  useRecordActionMutation: () => [pageMocks.recordAction],
  useSlot: (slot: string) => {
    pageMocks.requestedSlot = slot;
    return pageMocks.slotEntries;
  },
}));

vi.mock("./i18n", () => ({
  useMessagingT: () => (key: string) => key,
}));

import { ChannelsPage } from "./ChannelsPage";
import { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "./slots";

describe("ChannelsPage", () => {
  beforeEach(() => {
    pageMocks.resourceProps = null;
    pageMocks.columnFields = [];
    pageMocks.fieldNames = [];
    pageMocks.recordAction.mockClear();
    pageMocks.requestedSlot = "";
  });

  test("renders a model-driven channels page with addon toolbar actions", () => {
    render(<ChannelsPage />);

    expect(pageMocks.resourceProps).toMatchObject({
      resource: "messaging.Channel",
      placement: "inline",
      routed: true,
      hideCreate: true,
    });
    expect(pageMocks.requestedSlot).toBe(MESSAGING_CHANNEL_TOOLBAR_SLOT);
    expect(screen.getByText("Connect bridge")).toBeTruthy();
    expect(pageMocks.columnFields).toEqual(
      expect.arrayContaining(["sync_stage", "last_sync_status", "last_sync_items", "last_sync_completed_at"]),
    );
    expect(pageMocks.fieldNames).toEqual(
      expect.arrayContaining([
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
});
