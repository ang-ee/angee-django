// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const pageMocks = vi.hoisted(() => ({
  resourceProps: null as Record<string, unknown> | null,
  listProps: null as Record<string, unknown> | null,
  columns: [] as Array<{ field: string; header?: React.ReactNode; render?: (row: never) => React.ReactNode }>,
  fields: [] as string[],
  actions: 0,
  formProps: null as Record<string, unknown> | null,
  setStarred: vi.fn(async () => ({})),
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  createNamespaceT: () => () => (key: string) => key,
  Action: () => { pageMocks.actions += 1; return null; },
  Alert: ({ children }: { children?: React.ReactNode }) => <div role="alert">{children}</div>,
  Avatar: () => null,
  avatarInitials: (name: string) => name.slice(0, 1),
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { size?: string; variant?: string }) => {
    const { size: _size, variant: _variant, ...button } = props;
    return <button type="button" {...button}>{children}</button>;
  },
  cn: (...classes: unknown[]) => classes.filter(Boolean).join(" "),
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
  Glyph: ({ label }: { label?: string }) => (label ? <span>{label}</span> : null),
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
  MessagePartsView: ({ parts, onPreviewFile }: {
    parts: Array<{ id: string; fragment?: { text?: string }; file?: { filename?: string } }>;
    onPreviewFile?: (part: unknown) => void;
  }) => <div>
    {parts.map((part) => part.file
      ? <button key={part.id} type="button" onClick={() => onPreviewFile?.(part)}>{part.file.filename}</button>
      : <span key={part.id}>{part.fragment?.text}</span>)}
  </div>,
  PreviewPane: ({ file }: { file: { name: string; url: string } }) => <div data-testid="preview">{file.name} {file.url}</div>,
  RelativeTime: ({ value }: { value: string }) => <time>{value}</time>,
  registerForm: (resource: string, Component: React.ComponentType<Record<string, unknown>>) => ({ resource, Component }),
  useTrashActions: () => [{ id: "trash", label: "Move to trash" }, { id: "restore", label: "Restore" }],
  useResourceView: () => ({ state: { filter: {} }, baseFilter: {} }),
  useToast: () => ({ danger: vi.fn() }),
}));

vi.mock("@angee/metadata", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/metadata")>(),
  useModelMetadata: () => null,
  useResourceInvalidates: () => [],
}));

const sender = vi.hoisted(() => ({ id: "hdl-1", display_name: "Billing Desk", value: "billing@example.test", party_link_confirmed: false, party: null }));

vi.mock("@angee/parties", () => ({
  senderDisplayName: (sender: { display_name?: string; value?: string } | null, fallback = "") =>
    sender?.display_name || sender?.value || fallback,
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/refine")>(),
  useAuthoredQuery: () => ({
    data: { messages: [{
      id: "msg-1",
      title: "Invoice 42",
      preview: "Please find the invoice attached.",
      sent_at: "2026-10-01T09:00:00Z",
      created_at: "2026-10-01T09:00:01Z",
      starred: false,
      sender,
      participants: [
        { id: "ptp-1", role: "FROM", handle: sender },
        { id: "ptp-2", role: "TO", handle: { ...sender, id: "hdl-2", display_name: "Accounts", value: "ap@example.test" } },
      ],
      parts: [
        { id: "part-body", fragment: { text: "The complete retained message body." } },
        { id: "part-file", name: "invoice.pdf", type: "application/pdf", file: { filename: "invoice.pdf", url: "/files/invoice.pdf" } },
      ],
    }] },
    error: null,
    isFetching: false,
  }),
  useAuthoredMutation: () => [pageMocks.setStarred, { fetching: false }],
}));

vi.mock("./i18n", () => ({
  useMessagingT: () => (key: string) => key,
}));

vi.mock("./ThreadTranscript", () => ({ ThreadTranscript: () => null }));

import { messageForm } from "./MessageForm";
import { MessagesPage } from "./MessagesPage";
import { MESSAGE_SUMMARY_FIELDS } from "./MessageSummary";
import { ThreadsPage } from "./ThreadsPage";

describe("MessagesPage", () => {
  beforeEach(() => {
    pageMocks.resourceProps = null;
    pageMocks.listProps = null;
    pageMocks.columns = [];
    pageMocks.fields = [];
    pageMocks.actions = 0;
    pageMocks.formProps = null;
    pageMocks.setStarred.mockClear();
  });
  afterEach(cleanup);

  test("lists each message as an email-style row, grouped by channel", () => {
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
      fields: MESSAGE_SUMMARY_FIELDS,
      headerVisibility: "visually-hidden",
    });
    expect(pageMocks.columns).toHaveLength(1);
    const row = pageMocks.columns[0]!.render!({
      id: "msg-1",
      title: "Invoice 42",
      preview: "Please find the invoice attached.",
      starred: true,
      created_at: "2026-10-01T09:00:01Z",
      sender,
      channel: { display_name: "AP mailbox" },
    } as never);
    render(<>{row}</>);
    for (const text of ["Billing Desk", "Invoice 42", "Please find the invoice attached.", "message.starred", "AP mailbox"]) {
      expect(screen.getByText(text)).toBeTruthy();
    }
  });

  test("ThreadsPage selects the schema-owned channel kind without a vendor relation", () => {
    render(<ThreadsPage />);
    expect(pageMocks.resourceProps?.resource).toBe("messaging.Thread");
    const channelColumn = pageMocks.columns.find((column) => column.header === "threads.channelType");
    expect(channelColumn?.field).toBe("channel.kind");
    expect(pageMocks.columns.some((column) => column.field.startsWith("channel.vendor"))).toBe(false);
  });

  test("a Message record reads like an email above its Content and Envelope tabs", () => {
    render(<messageForm.Component resource="messaging.Message" id="msg-1" readOnly />);

    expect(pageMocks.fields).toEqual(expect.arrayContaining([
      "title", "status", "platform", "direction", "external_id",
    ]));
    // The reader shows the sender and the date, so the envelope does not repeat them.
    expect(pageMocks.fields).not.toContain("sender");
    expect(pageMocks.fields).not.toContain("sent_at");
    expect(pageMocks.fields).toContain("is_trashed");
    expect(pageMocks.actions).toBe(0);
    expect(pageMocks.formProps?.recordTabs).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "content", label: "messages.tabContent" }),
    ]));
    const formExtras = pageMocks.formProps?.formExtras as ((context: { recordId: string }) => React.ReactNode);
    render(<>{formExtras({ recordId: "msg-1" })}</>);
    expect(screen.getByText("Billing Desk")).toBeTruthy();
    expect(screen.getByText("billing@example.test")).toBeTruthy();
    expect(screen.getByText("message.to Accounts")).toBeTruthy();
    expect(screen.getByText("The complete retained message body.")).toBeTruthy();
    expect(screen.queryByTestId("preview")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "invoice.pdf" }));
    expect(screen.getByTestId("preview").textContent).toBe("invoice.pdf /files/invoice.pdf");
    fireEvent.click(screen.getByRole("button", { name: "message.closePreview" }));
    expect(screen.queryByTestId("preview")).toBeNull();
  });

  test("the reader stars the message for its reader", () => {
    render(<messageForm.Component resource="messaging.Message" id="msg-1" readOnly />);
    const formExtras = pageMocks.formProps?.formExtras as ((context: { recordId: string }) => React.ReactNode);
    render(<>{formExtras({ recordId: "msg-1" })}</>);

    fireEvent.click(screen.getByRole("button", { name: "message.star" }));
    expect(pageMocks.setStarred).toHaveBeenCalledWith({ id: "msg-1", starred: true });
  });

  test("moderates an editable Message through the shared trash verbs", () => {
    render(<messageForm.Component resource="messaging.Message" id="msg-1" />);

    expect(pageMocks.actions).toBe(2);
  });
});
