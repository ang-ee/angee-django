// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  importDocument: { operation: "import" },
  previewDocument: { operation: "preview" },
  previewPages: [] as Array<Record<string, unknown>>,
  runImport: vi.fn(),
  runPreview: vi.fn(),
}));

vi.mock("./documents", () => ({
  ImportImapSample: mocks.importDocument,
  PreviewImapSample: mocks.previewDocument,
}));

vi.mock("@angee/messaging", () => ({
  useMessagingT: () => (key: string, values?: Record<string, unknown>) =>
    values ? `${key}:${JSON.stringify(values)}` : key,
}));

vi.mock("@angee/refine", () => ({
  useAuthoredMutation: (document: unknown) => [
    document === mocks.previewDocument ? mocks.runPreview : mocks.runImport,
    { error: null, fetching: false },
  ],
}));

vi.mock("@angee/ui", async () => {
  const React = await import("react");
  return {
    Alert: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Button: ({ children, disabled, onClick, title, type }: React.ButtonHTMLAttributes<HTMLButtonElement>) =>
      <button type={type} disabled={disabled} onClick={onClick} title={title}>{children}</button>,
    Checkbox: ({ children }: { children: React.ReactNode }) => <label>{children}</label>,
    ControlBandProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    DialogForm: ({ children, footer, trigger }: {
      children: React.ReactNode; footer: React.ReactNode; trigger: React.ReactNode;
    }) => <div>{trigger}{children}{footer}</div>,
    FieldRow: ({ children, label }: { children: React.ReactNode; label: string }) =>
      <label>{label}{children}</label>,
    Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
    RecordActionTrigger: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
    RowsListView: ({ bulkActions, rows }: {
      bulkActions?: (selected: ReadonlySet<string>, clear: () => void) => React.ReactNode;
      rows: readonly { id: string; subject: string }[];
    }) => {
      const [selected, setSelected] = React.useState<ReadonlySet<string>>(new Set());
      const first = rows[0];
      return <div>
        <span>{rows.map((row) => row.subject).join(",")}</span>
        <button type="button" disabled={!first} onClick={() => first && setSelected(new Set([first.id]))}>
          Select first
        </button>
        {selected.size ? <span>{selected.size} selected</span> : null}
        {bulkActions?.(selected, () => setSelected(new Set()))}
      </div>;
    },
    errorMessage: () => "error",
    useRecordChromeContext: () => ({ recordId: "int_mail", record: { lifecycle: "PAUSED" } }),
  };
});

import { ImportImapSampleAction } from "./ImportImapSampleAction";

const message = (uid: number, subject: string) => ({
  uid, subject, sender: "sender@example.com", sent_at: "2026-09-23", size: 10, flags: [],
});

describe("ImportImapSampleAction snapshot selection", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.previewPages = [
      {
        mailbox: "INBOX", uidvalidity: 10, upper_uid: 3, total_count: 3,
        next_before_uid: 2, truncated: true,
        messages: [message(3, "Three"), message(2, "Two")],
      },
      {
        mailbox: "INBOX", uidvalidity: 10, upper_uid: 3, total_count: 3,
        next_before_uid: null, truncated: false, messages: [message(1, "One")],
      },
      {
        mailbox: "INBOX", uidvalidity: 10, upper_uid: 9, total_count: 1,
        next_before_uid: null, truncated: false, messages: [message(9, "Fresh")],
      },
    ];
    mocks.runPreview.mockReset().mockImplementation(async () => ({
      preview_imap_sample: mocks.previewPages.shift(),
    }));
    mocks.runImport.mockReset();
  });

  test("keeps selection while appending one snapshot and clears it for a fresh preview", async () => {
    render(<ImportImapSampleAction />);

    fireEvent.click(screen.getByRole("button", { name: "channel.imap.sample.preview" }));
    expect(await screen.findByText("Three,Two")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Select first" }));
    expect(screen.getByText("1 selected")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "channel.imap.sample.loadOlder" }));
    expect(await screen.findByText("Three,Two,One")).toBeTruthy();
    expect(screen.getByText("1 selected")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "channel.imap.sample.preview" }));
    expect(await screen.findByText("Fresh")).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("1 selected")).toBeNull());
  });
});
