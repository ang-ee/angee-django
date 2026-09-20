// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const pageMocks = vi.hoisted(() => ({
  resourceProps: null as Record<string, unknown> | null,
  listProps: null as Record<string, unknown> | null,
  columns: [] as Array<{ field: string; header?: React.ReactNode; render?: (row: never) => React.ReactNode }>,
  fields: [] as string[],
  actions: 0,
  formProps: null as Record<string, unknown> | null,
}));

vi.mock("@angee/ui", () => ({
  createNamespaceT: () => () => (key: string) => key,
  Action: () => { pageMocks.actions += 1; return null; },
  Column: (props: { field: string; header?: React.ReactNode; render?: (row: never) => React.ReactNode }) => {
    pageMocks.columns.push(props);
    return null;
  },
  Facet: () => null,
  Field: ({ name }: { name: string }) => { pageMocks.fields.push(name); return null; },
  Form: (props: Record<string, unknown>) => {
    pageMocks.formProps = props;
    return <section>{props.children as React.ReactNode}</section>;
  },
  Group: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  List: (props: Record<string, unknown>) => {
    pageMocks.listProps = props;
    return <section>{props.children as React.ReactNode}</section>;
  },
  ResourceList: (props: Record<string, unknown>) => {
    pageMocks.resourceProps = props;
    return <div>{props.children as React.ReactNode}</div>;
  },
  ErrorBanner: ({ description }: { description: React.ReactNode }) => <div>{description}</div>,
  LoadingPanel: ({ message }: { message: React.ReactNode }) => <div>{message}</div>,
  MessagePartsView: ({ parts }: { parts: Array<{ fragment?: { text?: string }; file?: { filename?: string } }> }) => <div>
    {parts.map((part, index) => <span key={index}>{part.fragment?.text || part.file?.filename}</span>)}
  </div>,
  registerForm: (resource: string, Component: React.ComponentType<Record<string, unknown>>) => ({ resource, Component }),
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: () => ({
    data: { messages: [{ id: "msg-1", parts: [
      { id: "part-body", fragment: { text: "The complete retained message body." } },
      { id: "part-file", file: { filename: "invoice.pdf" } },
    ] }] },
    error: null,
    isFetching: false,
  }),
}));

vi.mock("./i18n", () => ({
  useMessagingT: () => (key: string) => key,
}));

import { messageForm, MessagesPage } from "./MessagesPage";

describe("MessagesPage", () => {
  beforeEach(() => {
    pageMocks.resourceProps = null;
    pageMocks.listProps = null;
    pageMocks.columns = [];
    pageMocks.fields = [];
    pageMocks.actions = 0;
    pageMocks.formProps = null;
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
      defaultGroups: { list: { field: "channel" } },
    });
    const columnFields = pageMocks.columns.map((column) => column.field);
    expect(columnFields).toEqual(
      expect.arrayContaining([
        "title",
        "sender_name",
        "thread_title",
        "channel_vendor_name",
        "status",
        "sent_at",
      ]),
    );
    expect(columnFields).not.toContain("sender.value");
  });

  test("renders the same server-owned relation scalars used by ordering", () => {
    render(<MessagesPage />);

    for (const [header, field] of [
      ["messages.sender", "sender_name"],
      ["messages.thread", "thread_title"],
      ["messages.channelType", "channel_vendor_name"],
    ]) {
      const column = pageMocks.columns.find((column) => column.header === header);
      expect(column?.field).toBe(field);
      expect(column?.render).toBeUndefined();
    }
    expect(pageMocks.listProps?.fields).toBeUndefined();
  });

  test("registers a mutation-free Message peek with envelope, readable body, and structural details", () => {
    render(<messageForm.Component resource="messaging.Message" id="msg-1" readOnly />);

    expect(pageMocks.fields).toEqual(expect.arrayContaining([
      "title", "status", "sender", "sent_at", "platform", "direction", "external_id",
    ]));
    expect(pageMocks.fields).not.toContain("sender_name");
    expect(pageMocks.actions).toBe(0);
    expect(pageMocks.formProps?.recordTabs).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "content", label: "messages.tabContent" }),
    ]));
    const formExtras = pageMocks.formProps?.formExtras as ((context: { recordId: string }) => React.ReactNode);
    render(<>{formExtras({ recordId: "msg-1" })}</>);
    expect(screen.getByText("The complete retained message body.")).toBeTruthy();
    expect(screen.getByText("invoice.pdf")).toBeTruthy();
  });
});
