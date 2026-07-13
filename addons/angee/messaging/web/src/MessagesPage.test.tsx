// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const pageMocks = vi.hoisted(() => ({
  resourceProps: null as Record<string, unknown> | null,
  listProps: null as Record<string, unknown> | null,
  columns: [] as Array<{ field: string; header?: React.ReactNode; render?: (row: never) => React.ReactNode }>,
}));

vi.mock("@angee/ui", () => ({
  Action: () => null,
  Column: (props: { field: string; header?: React.ReactNode; render?: (row: never) => React.ReactNode }) => {
    pageMocks.columns.push(props);
    return null;
  },
  Facet: () => null,
  Field: () => null,
  Form: ({ children }: { children?: React.ReactNode }) => <section>{children}</section>,
  Group: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  List: (props: Record<string, unknown>) => {
    pageMocks.listProps = props;
    return <section>{props.children as React.ReactNode}</section>;
  },
  ResourceList: (props: Record<string, unknown>) => {
    pageMocks.resourceProps = props;
    return <div>{props.children as React.ReactNode}</div>;
  },
}));

vi.mock("./i18n", () => ({
  useMessagingT: () => (key: string) => key,
}));

import { MessagesPage } from "./MessagesPage";

describe("MessagesPage", () => {
  beforeEach(() => {
    pageMocks.resourceProps = null;
    pageMocks.listProps = null;
    pageMocks.columns = [];
  });

  test("uses readable relation axes for inbox grouping and sender display", () => {
    render(<MessagesPage />);

    expect(pageMocks.resourceProps).toMatchObject({
      resource: "messaging.Message",
      placement: "inline",
      routed: true,
      hideCreate: true,
    });
    expect(pageMocks.listProps).toMatchObject({
      resource: "messaging.Message",
      defaultGroups: { list: { field: "channel.display_name" } },
    });
    const columnFields = pageMocks.columns.map((column) => column.field);
    expect(columnFields).toEqual(
      expect.arrayContaining([
        "title",
        "sender.party.display_name",
        "thread.title.text",
        "status",
        "sent_at",
      ]),
    );
    expect(columnFields).not.toContain("sender.value");
  });

  test("keeps the envelope name for an unconfirmed 1.0 email-match auto-link", () => {
    render(<MessagesPage />);

    const sender = pageMocks.columns.find((column) => column.header === "messages.sender");
    expect(sender?.render?.({
      sender: {
        party: { display_name: "Ada Curated" },
        party_link_confirmed: false,
        display_name: "Ada Envelope",
        value: "ada@example.com",
      },
    } as never)).toBe("Ada Envelope");
    expect(pageMocks.listProps?.fields).toEqual(
      expect.arrayContaining(["sender.party_link_confirmed", "sender.display_name", "sender.value"]),
    );
  });

  test("prefers the curated party name after the resolving link is confirmed", () => {
    render(<MessagesPage />);

    const sender = pageMocks.columns.find((column) => column.header === "messages.sender");
    expect(sender?.render?.({
      sender: {
        party: { display_name: "Ada Curated" },
        party_link_confirmed: true,
        display_name: "Ada Envelope",
        value: "ada@example.com",
      },
    } as never)).toBe("Ada Curated");
  });
});
