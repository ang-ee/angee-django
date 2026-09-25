// @vitest-environment happy-dom

import { AppRuntimeProvider, ToastProvider } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => {
  // Transport fixtures only: the real query, mutation, dialog, and table owners
  // run below even when the host's generated documents lag this worktree.
  const document = (operation: string, name: string) => ({
    kind: "Document", definitions: [{ kind: "OperationDefinition", operation,
      name: { kind: "Name", value: name },
      selectionSet: { kind: "SelectionSet", selections: [{ kind: "Field", name: { kind: "Name", value: "fixture" } }] },
    }],
  });
  return {
    importDocument: document("mutation", "ImportImapSample"),
    previewDocument: document("query", "PreviewImapSample"),
    previewPages: [] as Array<Record<string, unknown>>,
    custom: vi.fn(),
    actor: "user-1",
  };
});

vi.mock("./documents", () => ({
  ImportImapSample: mocks.importDocument,
  PreviewImapSample: mocks.previewDocument,
}));

vi.mock("@angee/app", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/app")>(),
  useAuth: () => ({ user: { id: mocks.actor } }),
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const { createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal, {
    useRecordChromeContext: () => ({ recordId: "int_mail", record: { lifecycle: "PAUSED" } }),
  });
});

import { ImportImapSampleAction } from "./ImportImapSampleAction";
import { enMessagingImapMessages } from "./i18n";

const { Provider, clearClients, clients } = createUiTestProviders({
  dataProvider: { custom: mocks.custom },
  queryClientConfig: { defaultOptions: { queries: { retry: false }, mutations: { retry: false } } },
});
const i18n = {
  getFixedT: (_language: unknown, namespace: string) => (key: string, options: Record<string, unknown> = {}) => {
    const template = (namespace === "messaging" ? enMessagingImapMessages[key] : undefined) ?? options.defaultValue ?? key;
    return String(template).replace(/\{(\w+)\}/g, (match, name: string) => String(options[name] ?? match));
  },
};
const message = (uid: number, subject = `Message ${uid}`) => ({
  uid, subject, sender: "sender@example.com", sent_at: "2026-09-23", size: 10, flags: [],
});
const page = (uids: number[], nextBeforeUid: number | null = null, total = uids.length) => ({
  mailbox: "INBOX", uidvalidity: 10, upper_uid: 100, total_count: total,
  next_before_uid: nextBeforeUid, messages: uids.map((uid) => message(uid)),
});
const previewCalls = () => mocks.custom.mock.calls.filter(([request]) => request.meta.gqlQuery);

function TestDialog() {
  return <Provider><AppRuntimeProvider runtime={{ i18n }}><ToastProvider>
    <ImportImapSampleAction />
  </ToastProvider></AppRuntimeProvider></Provider>;
}

async function openDialog() {
  const view = render(<TestDialog />);
  fireEvent.click(screen.getByRole("button", { name: "Import historical sample" }));
  await screen.findByRole("dialog", { name: "Import historical messages" });
  return view;
}

async function preview() {
  fireEvent.click(screen.getByRole("button", { name: "Preview messages" }));
  await screen.findByText(/Mailbox snapshot/);
  await waitFor(() => expect(screen.getByRole("button", { name: "Preview messages" }).hasAttribute("disabled")).toBe(false));
}

describe("ImportImapSampleAction snapshot selection", () => {
  afterEach(() => { cleanup(); clearClients(); });

  beforeEach(() => {
    mocks.actor = "user-1";
    mocks.previewPages = [page([3, 2], 2, 3), page([1], null, 3), {
      ...page([3]), messages: [message(3, "Fresh observation of UID 3")],
    }];
    mocks.custom.mockImplementation(async ({ meta }) => meta.gqlQuery
      ? { data: { preview_imap_sample: mocks.previewPages.shift() } }
      : { data: { import_imap_sample: { imported_uids: [3], missing_uids: [], flags_unchanged: true } } });
  });

  test("all dates has one accessible name, disables date fields, and omits the date window", async () => {
    await openDialog();
    expect(previewCalls()).toHaveLength(0);
    fireEvent.click(screen.getByRole("checkbox", { name: "All dates in this mailbox" }));
    expect(screen.getByLabelText("Since").hasAttribute("disabled")).toBe(true);
    expect(screen.getByLabelText("Before").hasAttribute("disabled")).toBe(true);
    await preview();
    expect(previewCalls()[0]?.[0].meta.gqlVariables).toEqual({
      id: "int_mail", mailbox: "INBOX", since: null, before: null, allDates: true, limit: 20,
    });
    expect(screen.getByText("Mailbox snapshot 10:100. Loaded 2 of 3 matching messages (initial count, less confirmed missing messages).")).toBeTruthy();
  });

  test("loads older rows without dropping selection, then resets pages and selection on a fresh preview", async () => {
    await openDialog();
    await preview();
    fireEvent.click(within(screen.getByRole("row", { name: /Message 3/ })).getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Import 1 selected" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Load older messages" }));
    expect(await screen.findByText("Message 1")).toBeTruthy();
    expect(screen.getByText("Message 3")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import 1 selected" })).toBeTruthy();
    expect(previewCalls()[1]?.[0].meta.gqlVariables).toMatchObject({
      uidvalidity: 10, upperUid: 100, beforeUid: 2, totalCount: 3,
    });
    await preview();
    expect(await screen.findByText("Fresh observation of UID 3")).toBeTruthy();
    expect(screen.queryByText("Message 3")).toBeNull();
    expect(screen.queryByRole("button", { name: "Import 1 selected" })).toBeNull();
    expect(previewCalls()).toHaveLength(3);
  });

  test("an empty continuation keeps loaded rows and selected IDs", async () => {
    mocks.previewPages = [page([3, 2], 2, 3), page([], null, 2)];
    await openDialog();
    await preview();
    fireEvent.click(within(screen.getByRole("row", { name: /Message 3/ })).getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Load older messages" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Load older messages" })).toBeNull());
    expect(screen.getByText("Message 3")).toBeTruthy();
    expect(screen.getByText("Message 2")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import 1 selected" })).toBeTruthy();
    expect(screen.getByText(/Loaded 2 of 2 matching messages/)).toBeTruthy();
  });

  test("an empty first page retains mailbox identity without a continuation cursor", async () => {
    mocks.previewPages = [page([], null, 0)];
    await openDialog();
    await preview();
    expect(screen.getByText("Mailbox snapshot 10:100. Loaded 0 of 0 matching messages (initial count, less confirmed missing messages).")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Load older messages" })).toBeNull();
  });

  test("imports selected UIDs through the mutation without refreshing the mailbox", async () => {
    await openDialog();
    await preview();
    fireEvent.click(within(screen.getByRole("row", { name: /Message 3/ })).getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Import 1 selected" }));
    expect(await screen.findByText(/Imported 1; 0 were no longer available/)).toBeTruthy();
    expect(mocks.custom.mock.calls.find(([request]) => request.meta.gqlMutation)?.[0].meta.gqlVariables).toEqual({
      id: "int_mail", mailbox: "INBOX", uidvalidity: 10, uids: [3],
    });
    expect(previewCalls()).toHaveLength(1);
  });

  test.each(["fresh preview", "close and reopen", "changed input"])("%s clears the native failed-import error", async (action) => {
    await openDialog();
    await preview();
    fireEvent.click(within(screen.getByRole("row", { name: /Message 3/ })).getByRole("checkbox"));
    mocks.custom.mockRejectedValueOnce(new Error("Mailbox import failed."));
    fireEvent.click(screen.getByRole("button", { name: "Import 1 selected" }));
    expect(await screen.findByText("Mailbox import failed.")).toBeTruthy();

    if (action === "fresh preview") {
      await preview();
      expect(await screen.findByText("Message 1")).toBeTruthy();
    } else if (action === "close and reopen") {
      fireEvent.click(screen.getByRole("button", { name: "Close" }));
      fireEvent.click(screen.getByRole("button", { name: "Import historical sample" }));
      await screen.findByRole("dialog", { name: "Import historical messages" });
    } else {
      fireEvent.change(screen.getByLabelText("Mailbox"), { target: { value: "Archive" } });
    }
    expect(screen.queryByText("Mailbox import failed.")).toBeNull();
    expect(previewCalls()).toHaveLength(action === "fresh preview" ? 2 : 1);
  });

  test("closing and reopening a pending import keeps the native busy state until it completes", async () => {
    await openDialog();
    await preview();
    fireEvent.click(within(screen.getByRole("row", { name: /Message 3/ })).getByRole("checkbox"));
    let finishImport!: () => void;
    mocks.custom.mockImplementationOnce(() => new Promise((resolve) => {
      finishImport = () => resolve({ data: { import_imap_sample: {
        imported_uids: [3], missing_uids: [], flags_unchanged: true,
      } } });
    }));
    fireEvent.click(screen.getByRole("button", { name: "Import 1 selected" }));
    await screen.findByRole("button", { name: "Importing…" });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Import historical sample" }));
    await screen.findByRole("dialog", { name: "Import historical messages" });

    const previewButton = screen.getByRole("button", { name: "Preview messages" });
    expect(previewButton.hasAttribute("disabled")).toBe(true);
    expect(screen.getByLabelText("Mailbox").hasAttribute("disabled")).toBe(true);
    expect(screen.queryByRole("button", { name: "Import 1 selected" })).toBeNull();
    fireEvent.click(previewButton);
    expect(previewCalls()).toHaveLength(1);

    await act(async () => { finishImport(); });
    expect(await screen.findByText(/Imported 1; 0 were no longer available/)).toBeTruthy();
    expect(previewButton.hasAttribute("disabled")).toBe(false);
    await preview();
    fireEvent.click(within(await screen.findByRole("row", { name: /Message 1/ })).getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Import 1 selected" }).hasAttribute("disabled")).toBe(false);
    expect(previewCalls()).toHaveLength(2);
  });

  test("disables bulk import above 50 selected rows across loaded pages", async () => {
    mocks.previewPages = [page(Array.from({ length: 50 }, (_, index) => 100 - index), 51, 51), page([50], null, 51)];
    await openDialog();
    fireEvent.change(screen.getByRole("spinbutton", { name: "Messages per page" }), { target: { value: "50" } });
    await preview();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select all rows on this page" }));
    expect(screen.getByRole("button", { name: "Import 50 selected" }).hasAttribute("disabled")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Load older messages" }));
    await screen.findByText(/Loaded 51 of 51/);
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    fireEvent.click(within(screen.getByRole("row", { name: /Message 50/ })).getByRole("checkbox"));
    const button = screen.getByRole("button", { name: "Import 51 selected" });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(button.getAttribute("title")).toBe("Import at most 50 selected messages at a time.");
  });

  test.each([
    ["Mailbox", ""], ["Since", "2026-01-01"], ["Before", "2026-10-01"], ["Messages per page", "10"],
  ])("changing %s invalidates the preview and selection without a mailbox request", async (label, value) => {
    await openDialog();
    await preview();
    fireEvent.click(within(screen.getByRole("row", { name: /Message 3/ })).getByRole("checkbox"));
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
    expect(screen.queryByText("Message 3")).toBeNull();
    expect(screen.queryByRole("button", { name: "Import 1 selected" })).toBeNull();
    expect(previewCalls()).toHaveLength(1);
  });

  test.each(["", "0", "51", "1.5"])("an invalid limit %s stays editable and cannot start a preview", async (value) => {
    await openDialog();
    const input = screen.getByRole("spinbutton", { name: "Messages per page" }) as HTMLInputElement;
    fireEvent.change(input, { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "Preview messages" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Choose a whole number between 1 and 50 messages per page.");
    expect(input.value).toBe(value);
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.getAttribute("aria-describedby")).toBe(screen.getByRole("alert").id);
    expect(previewCalls()).toHaveLength(0);
    fireEvent.change(input, { target: { value: "1" } });
    await preview();
    expect(previewCalls()[0]?.[0].meta.gqlVariables.limit).toBe(1);
  });

  test("a blank mailbox blocks submission and a corrected mailbox is trimmed", async () => {
    await openDialog();
    fireEvent.change(screen.getByLabelText("Mailbox"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Preview messages" }));
    const error = await screen.findByRole("alert");
    expect(error.textContent).toBe("Choose a mailbox and a valid date window.");
    expect(screen.getByLabelText("Mailbox").getAttribute("aria-describedby")).toBe(error.id);
    expect(previewCalls()).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Mailbox"), { target: { value: " Archive " } });
    await preview();
    expect(previewCalls()[0]?.[0].meta.gqlVariables.mailbox).toBe("Archive");
    fireEvent.click(within(screen.getByRole("row", { name: /Message 3/ })).getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Import 1 selected" }));
    await screen.findByText(/Imported 1; 0 were no longer available/);
    expect(mocks.custom.mock.calls.find(([request]) => request.meta.gqlMutation)?.[0].meta.gqlVariables.mailbox).toBe("Archive");
  });

  test("changing all dates invalidates the preview without querying", async () => {
    await openDialog();
    await preview();
    fireEvent.click(within(screen.getByRole("row", { name: /Message 3/ })).getByRole("checkbox"));
    fireEvent.click(screen.getByRole("checkbox", { name: "All dates in this mailbox" }));
    expect(screen.queryByText("Message 3")).toBeNull();
    expect(screen.queryByRole("button", { name: "Import 1 selected" })).toBeNull();
    expect(previewCalls()).toHaveLength(1);
  });

  test.each([
    ["2026-02-02", "2026-02-01", "Choose a mailbox and a valid date window."],
    ["2024-01-01", "2026-02-01", "The preview window cannot exceed 366 days."],
  ])("validates the date window %s – %s without querying", async (since, before, error) => {
    await openDialog();
    fireEvent.change(screen.getByLabelText("Since"), { target: { value: since } });
    fireEvent.change(screen.getByLabelText("Before"), { target: { value: before } });
    fireEvent.click(screen.getByRole("button", { name: "Preview messages" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(error);
    for (const label of ["Since", "Before"]) {
      expect(screen.getByLabelText(label).getAttribute("aria-invalid")).toBe("true");
      expect(screen.getByLabelText(label).getAttribute("aria-describedby")).toBe(alert.id);
    }
    expect(previewCalls()).toHaveLength(0);
    fireEvent.click(screen.getByRole("checkbox", { name: "All dates in this mailbox" }));
    await preview();
    expect(previewCalls()[0]?.[0].meta.gqlVariables).toMatchObject({ since: null, before: null, allDates: true });
  });

  test("changing actors does not probe until another explicit preview", async () => {
    const view = await openDialog();
    await preview();
    mocks.actor = "user-2";
    await act(async () => { view.rerender(<TestDialog />); });
    expect(previewCalls()).toHaveLength(1);
    await preview();
    expect(previewCalls()).toHaveLength(2);
  });

  test("focus, reconnect, invalidation and reopening do not implicitly probe a preview", async () => {
    await openDialog();
    await preview();
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("online"));
      await clients[0]?.invalidateQueries();
    });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Import historical sample" }));
    await screen.findByRole("dialog", { name: "Import historical messages" });
    expect(previewCalls()).toHaveLength(1);
    expect(screen.queryByText("Message 3")).toBeNull();
  });
});
