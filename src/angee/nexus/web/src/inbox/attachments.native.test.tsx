// @vitest-environment happy-dom
import { ShellPageTestProviders } from "@angee/app/testing";
import { ResourceViewProvider, ToastProvider, createRouteHref, type ChatterViewContext } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import type { ReactNode } from "react";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { authoredQueryReadsLiveChange } from "@angee/refine";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("@angee/app", async (original) => ({
  ...(await original<typeof import("@angee/app")>()),
  useAuth: () => ({ user: { id: "actor" } }),
}));

import { InboxConversation } from "./Conversation";
import { InboxConnections, InboxConnectionsCount, InboxMessage, InboxMessageUses, InboxRelatedTarget, InboxTranscriptMatches } from "./documents";
import { InboxMessageReader } from "./MessageReader";
import { InboxRelatedPane } from "./Related";
import { FileMessages, useFileMessageCount } from "./FileMessages";
import type { InboxNavigation, InboxScope } from "./state";

const { Provider, createClient, clearClients } = createUiTestProviders({ apiUrl: "test://inbox-files", providerNames: [] });
const originalScrollTo = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollTo");
// The transcript virtualizes its rows over the measured viewport, which happy-dom
// reports as zero-sized; give elements a 640 x 640 viewport so the rows render,
// whichever box the virtualizer measures (@tanstack/virtual-core reads the client
// box since 3.19.6, the offset box before).
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(640);
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(640);
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(640);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(640);
});
afterEach(() => {
  cleanup(); clearClients(); vi.restoreAllMocks();
  if (originalScrollTo) Object.defineProperty(HTMLElement.prototype, "scrollTo", originalScrollTo);
  else Reflect.deleteProperty(HTMLElement.prototype, "scrollTo");
});

const pdf = (n: number) => ({
  id: `part-${n}`, name: `renewal-${n}.pdf`, type: "application/pdf", position: n, role: "BODY",
  disposition: "ATTACHMENT", cid: "", parent: null, fragment: null,
  file: {
    id: `file-${n}`, filename: `renewal-${n}.pdf`, title: "", size_bytes: "2048", url: `/storage/download/token-${n}/renewal-${n}.pdf`,
    mime_type: { mime_type: "application/pdf", label: "PDF" },
  },
});
const sender = { id: "handle-1", display_name: "Ada", value: "ada@example.com", party_link_confirmed: false, avatar: null, party: null };
const parent = {
  id: "parent-1", preview: "Meet at Casa Cosecha.", message_type: "COMMENT",
  sender: { ...sender, display_name: "Dane" }, thread: { id: "thread-1" },
};
const message = {
  id: "message-1", feed_order_key: "v1:0001", direction: "INBOUND", preview: "Both renewals attached", title: "Renewal",
  message_type: "EMAIL", platform: "email", status: "RECEIVED", starred: false, sent_at: "2026-10-01T10:00:00Z",
  created_at: "2026-10-01T10:00:00Z", received_at: "2026-10-01T10:00:00Z", external_id: "", sender,
  thread: { id: "thread-1", title: null, modality: "EMAIL", conversation_label: "Renewals" }, channel: null,
  participants: [], parts: [pdf(1), pdf(2)], reaction_groups: [], parent,
};

function fixture(uses: { part_id: string; target: string; count: number }[] = [], fileUses = false, status = false, survivor?: string, transcriptMessages = [message], matchingVisible: string[] = []) {
  const source = status ? { ...message, title: "", platform: "WHATSAPP",
    thread: { ...message.thread, modality: "STATUS", conversation_label: "Status updates" },
    channel: { id: "acc-one", display_name: "Connected account" } }
    : survivor ? { ...message, merged_into: { id: survivor } } : message;
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const dataProvider = { custom: vi.fn(async ({ meta }) => {
    if (meta?.gqlQuery === InboxTranscriptMatches) {
      const at = meta.gqlVariables?.at;
      const anchorIndex = transcriptMessages.findIndex((row) => row.id === at);
      const matching = matchingVisible.map((id) => transcriptMessages.findIndex((row) => row.id === id))
        .filter((index) => index >= 0).sort((left, right) => left - right);
      return { data: { inbox_transcript_matches: {
        thread: { id: "thread-1", conversation_label: "Renewals" }, count: matchingVisible.length,
        total: transcriptMessages.length, visible: matchingVisible.filter((id) => meta?.gqlVariables?.visible.includes(id)),
        previous: transcriptMessages[(anchorIndex < 0 ? matching.at(-1) : matching.filter((index) => index < anchorIndex).at(-1)) ?? -1]?.id ?? null,
        next: transcriptMessages[(anchorIndex < 0 ? matching[0] : matching.find((index) => index > anchorIndex)) ?? -1]?.id ?? null,
      } } };
    }
    if (meta?.gqlQuery === InboxMessage) {
      return { data: { inbox_message: { matches: !status, message: source } } };
    }
    if (meta?.gqlQuery === InboxMessageUses) return { data: { inbox_message_uses: uses } };
    if (meta?.gqlQuery === InboxRelatedTarget) {
      const part = pdf(2);
      return { data: { inbox_related_target: {
        id: "file:file-2", kind: "file", label: part.name, count: 1, unit: "messages",
        source, part, file: part.file, fragment: null,
      } } };
    }
    if (meta?.gqlQuery === InboxConnections) {
      return { data: { inbox_connections: { rows: fileUses ? [{ id: "use-1", message: source, part: pdf(2) }] : [], count: fileUses ? 1 : 0, message_count: fileUses ? 1 : 0 } } };
    }
    if (meta?.gqlQuery === InboxConnectionsCount) {
      return { data: { inbox_connections: { message_count: fileUses ? 1 : 0 } } };
    }
    return { data: { thread_message_feed: {
      messages: transcriptMessages, count: transcriptMessages.length, older_cursor: null, has_older: false, has_more_in_window: false,
      has_older_than_through: false, newer_cursor: null, has_newer: false, has_newer_than_before: false,
    } } };
  }) } satisfies RefineTestDataProvider;
  function Providers({ children }: { children: ReactNode }) {
    return <Provider dataProvider={dataProvider} queryClient={client} liveProvider={{ subscribe: vi.fn(() => "inbox-files"), unsubscribe: vi.fn() }}
      authProvider={{ login: async () => ({ success: true }), logout: async () => ({ success: true }), check: async () => ({ authenticated: true }), onError: async () => ({}) }}
    ><ResourceViewProvider scope="local">{children}</ResourceViewProvider></Provider>;
  }
  const navigation = {
    thread: "thread-1", at: "", message: "message-1", part: "", related: "file:file-2", relatedFrom: "message-1",
    showUpdates: vi.fn(), updatesHref: vi.fn(() => "/nexus/inbox?kind=updates"),
    patch: vi.fn(), previewFile: vi.fn(), read: vi.fn(), results: vi.fn(), conversation: vi.fn(), connect: vi.fn(), openInContext: vi.fn(),
    href: (values: Record<string, string | undefined>) => createRouteHref([{ name: "inbox", path: "/nexus/inbox" }])("inbox", {}, values),
  };
  const scope = { coverage: { timezone: "UTC" }, search: {}, sender: "", circle: "" };
  return {
    custom: dataProvider.custom,
    client,
    wrapper: Providers,
    navigation,
    props: { navigation: navigation as unknown as InboxNavigation, scope: scope as unknown as InboxScope },
  };
}

test("an attachment in the conversation previews in Related", async () => {
  const inbox = fixture();
  render(<InboxConversation {...inbox.props} />, { wrapper: inbox.wrapper });

  fireEvent.click(await screen.findByRole("button", { name: /renewal-2\.pdf/ }));

  expect(screen.getAllByRole("toolbar")).toHaveLength(1);
  expect(inbox.navigation.previewFile).toHaveBeenCalledWith("file-2", "message-1");
  expect(inbox.navigation.patch).not.toHaveBeenCalled();
});

test("a 200-message conversation reads matches once after positioning", async () => {
  const messages = Array.from({ length: 200 }, (_, index) => ({
    ...message, id: `message-${index}`, feed_order_key: `v1:${String(index).padStart(4, "0")}`,
  }));
  const inbox = fixture([], false, false, undefined, messages);
  render(<InboxConversation {...inbox.props} />, { wrapper: inbox.wrapper });

  await waitFor(() => expect(inbox.custom.mock.calls.filter(([request]) =>
    request.meta?.gqlQuery === InboxTranscriptMatches)).toHaveLength(1));
  const matchesRequest = inbox.custom.mock.calls.find(([request]) =>
    request.meta?.gqlQuery === InboxTranscriptMatches)![0];
  expect(matchesRequest.meta?.gqlVariables?.visible).toHaveLength(200);
  expect(matchesRequest.meta?.gqlVariables?.at).toBe("");
  await waitFor(() => expect(screen.getByText("Renewals")).toBeTruthy());
  expect(inbox.custom.mock.calls.filter(([request]) =>
    request.meta?.gqlQuery === InboxTranscriptMatches)).toHaveLength(1);
});

test("scrolling a 200-message transcript sends no matches read; previous and next use the viewport", async () => {
  const messages = Array.from({ length: 200 }, (_, index) => ({
    ...message, id: `message-${index}`, feed_order_key: `v1:${String(index).padStart(4, "0")}`,
  }));
  const inbox = fixture([], false, false, undefined, messages, messages.slice(1).map((row) => row.id));
  render(<InboxConversation {...inbox.props} />, { wrapper: inbox.wrapper });

  await waitFor(() => expect(inbox.custom.mock.calls.some(([request]) =>
    request.meta?.gqlQuery === InboxTranscriptMatches)).toBe(true));
  const matchReads = inbox.custom.mock.calls.filter(([request]) => request.meta?.gqlQuery === InboxTranscriptMatches);
  expect(matchReads[0]![0].meta?.gqlVariables?.visible).toHaveLength(200);
  const transcript = document.querySelector("ul[aria-label] > li[data-index]")?.parentElement?.parentElement;
  expect(transcript).toBeTruthy();
  transcript!.scrollTop = 96 * 150;
  fireEvent.scroll(transcript!);
  expect(inbox.custom.mock.calls.filter(([request]) => request.meta?.gqlQuery === InboxTranscriptMatches)).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Previous match" }));
  await waitFor(() => expect(inbox.navigation.patch).toHaveBeenCalledTimes(1));
  const viewportId = inbox.custom.mock.calls.filter(([request]) => request.meta?.gqlQuery === InboxTranscriptMatches).at(-1)![0].meta?.gqlVariables?.at as string;
  const viewportIndex = Number(viewportId.slice("message-".length));
  expect(viewportIndex).toBeGreaterThan(1);
  expect(viewportIndex).toBeLessThan(199);
  expect(inbox.navigation.patch).toHaveBeenCalledWith({ at: `message:message-${viewportIndex - 1}` });
  fireEvent.click(screen.getByRole("button", { name: "Next match" }));
  await waitFor(() => expect(inbox.navigation.patch).toHaveBeenCalledTimes(2));
  const nextViewportId = inbox.custom.mock.calls.filter(([request]) => request.meta?.gqlQuery === InboxTranscriptMatches).at(-1)![0].meta?.gqlVariables?.at as string;
  const nextViewportIndex = Number(nextViewportId.slice("message-".length));
  expect(inbox.navigation.patch).toHaveBeenCalledWith({ at: `message:message-${nextViewportIndex + 1}` });
  expect(inbox.custom.mock.calls.filter(([request]) => request.meta?.gqlQuery === InboxTranscriptMatches)).toHaveLength(3);
  transcript!.scrollTop = 1_000_000;
  fireEvent.scroll(transcript!);
  await waitFor(() => expect(document.querySelector('li[data-index="199"]')).toBeTruthy());
  expect(screen.getAllByText("Matches current filters").length).toBeGreaterThan(0);
  expect(inbox.custom.mock.calls.filter(([request]) => request.meta?.gqlQuery === InboxTranscriptMatches)).toHaveLength(3);
});

test("opening at a message anchor retains the positioned scroll element and lands on the message", async () => {
  const messages = Array.from({ length: 200 }, (_, index) => ({
    ...message, id: `message-${index}`, feed_order_key: `v1:${String(index).padStart(4, "0")}`,
  }));
  const inbox = fixture([], false, false, undefined, messages);
  inbox.navigation.at = "message:message-150";
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(200 * 640);
  const positionedElements: HTMLElement[] = [];
  const matchesAtPosition: number[] = [];
  Object.defineProperty(HTMLElement.prototype, "scrollTo", { configurable: true, value: function(this: HTMLElement, options: ScrollToOptions) {
    positionedElements.push(this);
    matchesAtPosition.push(inbox.custom.mock.calls.filter(([request]) => request.meta?.gqlQuery === InboxTranscriptMatches).length);
    this.scrollTop = options.top ?? 0;
    queueMicrotask(() => this.dispatchEvent(new Event("scroll")));
  } });
  render(<InboxConversation {...inbox.props} />, { wrapper: inbox.wrapper });

  await waitFor(() => expect(document.querySelector('li[data-index="150"] [aria-current="true"]')).toBeTruthy());
  const transcript = document.querySelector('li[data-index="150"]')?.parentElement?.parentElement;
  expect(positionedElements[0]).toBe(transcript);
  expect(matchesAtPosition[0]).toBe(0);
  expect(transcript?.scrollTop).toBeGreaterThan(0);
  expect(inbox.custom.mock.calls.filter(([request]) => request.meta?.gqlQuery === InboxTranscriptMatches)).toHaveLength(1);
  expect(inbox.custom.mock.calls.find(([request]) => request.meta?.gqlQuery === InboxTranscriptMatches)?.[0].meta?.gqlVariables?.at)
    .toBe("message-150");
});

test("transcript matches bound the annotation window after older history loads", async () => {
  const messages = Array.from({ length: 1001 }, (_, index) => ({
    ...message, id: `message-${index}`, feed_order_key: `v1:${String(index).padStart(4, "0")}`,
  }));
  const inbox = fixture([], false, false, undefined, messages);
  render(<InboxConversation {...inbox.props} />, { wrapper: inbox.wrapper });

  await waitFor(() => expect(inbox.custom.mock.calls.some(([request]) =>
    request.meta?.gqlQuery === InboxTranscriptMatches)).toBe(true));
  const reads = () => inbox.custom.mock.calls.filter(([request]) => request.meta?.gqlQuery === InboxTranscriptMatches);
  expect(reads()).toHaveLength(1);
  expect(reads()[0]![0].meta?.gqlVariables?.visible).toHaveLength(1000);
  const transcript = document.querySelector("ul[aria-label] > li[data-index]")?.parentElement?.parentElement;
  expect(transcript).toBeTruthy();
  transcript!.scrollTop = 96 * 700;
  fireEvent.scroll(transcript!);
  expect(reads()).toHaveLength(1);
});

test("an attachment in the reader previews in Related and leaves the message full width", async () => {
  const inbox = fixture();
  render(<InboxMessageReader {...inbox.props} />, { wrapper: inbox.wrapper });

  fireEvent.click(await screen.findByRole("button", { name: /renewal-2\.pdf/ }));

  expect(inbox.navigation.previewFile).toHaveBeenCalledWith("file-2", "message-1");
  expect(inbox.navigation.patch).not.toHaveBeenCalled();
  expect(screen.queryByRole("complementary", { name: "Attachment preview" })).toBeNull();
});

test("the reader retains the quoted author and opens the parent in its conversation", async () => {
  const inbox = fixture();
  render(<InboxMessageReader {...inbox.props} />, { wrapper: inbox.wrapper });

  fireEvent.click(await screen.findByRole("button", { name: /Replying to Dane Meet at Casa Cosecha/ }));

  expect(inbox.navigation.openInContext).toHaveBeenCalledWith(parent);
});

test("Related shows a pinned file's preview in the pane", async () => {
  const inbox = fixture();
  // Related's uses list keeps its collection state in the route.
  const root = createRootRoute({ component: () => <InboxRelatedPane navigation={inbox.props.navigation} /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<ShellPageTestProviders><ToastProvider><RouterProvider router={router} /></ToastProvider></ShellPageTestProviders>, { wrapper: inbox.wrapper });

  expect(await screen.findByRole("region", { name: "File preview" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "renewal-2.pdf" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Renewal.*Original source/ }));
  expect(inbox.navigation.read).toHaveBeenCalledWith("message-1", "part-2");
  fireEvent.click(screen.getByRole("button", { name: "Open conversation" }));
  expect(inbox.navigation.conversation).toHaveBeenCalledWith("thread-1", "message-1");
});

test("repeated parts of one target keep the server's distinct message count", async () => {
  const inbox = fixture([
    { part_id: "part-1", target: "file:file-2", count: 2 },
    { part_id: "part-2", target: "file:file-2", count: 2 },
    { part_id: "part-3", target: "fragment:single", count: 1 },
  ]);
  render(<InboxMessageReader {...inbox.props} />, { wrapper: inbox.wrapper });
  await screen.findByRole("button", { name: /renewal-2\.pdf/ });
  expect(inbox.custom.mock.calls.some(([request]) => request.meta?.gqlQuery === InboxMessageUses)).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Show message links" }));
  const use = await screen.findByRole("button", { name: "renewal-2.pdf 2 accessible message uses" });
  expect(screen.getAllByRole("button", { name: /accessible message uses/ })).toHaveLength(1);
  fireEvent.click(use);
  expect(inbox.navigation.connect).toHaveBeenCalledWith("file:file-2", "message-1");
});

const fileContext: ChatterViewContext = {
  pathname: "/storage/file-2", params: { id: "file-2" },
  route: { name: "storage.file", path: "/storage/$id", viewType: "storage/file", modelLabel: "storage.File" },
  view: { kind: "record", type: "storage/file", sqid: "file-2" },
};

test("the message read names its own thread, party and files for live invalidation", async () => {
  const inbox = fixture();
  render(<InboxMessageReader {...inbox.props} />, { wrapper: inbox.wrapper });
  await screen.findByRole("button", { name: /renewal-2\.pdf/ });
  await waitFor(() => {
    const read = inbox.client.getQueryCache().findAll().find((entry) =>
      (entry.meta?.angeeRelatedModels as string[] | undefined)?.includes("messaging.Thread")
      && (entry.meta?.angeeRecords as { model: string; id: string }[] | undefined)?.some((record) => record.id === "message-1"));
    expect(read?.meta?.angeeRecords).toEqual(expect.arrayContaining([
      { model: "messaging.Message", id: "message-1" },
      { model: "messaging.Thread", id: "thread-1" },
      { model: "storage.File", id: "file-2" },
    ]));
    expect(read?.meta?.angeeBroadModels).toEqual(expect.arrayContaining([
      "messaging.Part", "messaging.Participant", "messaging.MessageStar", "messaging.MessageEdge",
    ]));
  });
});

test.each(["messaging.Part", "messaging.Participant", "messaging.MessageStar", "messaging.MessageEdge"])(
  "%s changes retain the reader's model fallback", async (model) => {
    const inbox = fixture();
    render(<InboxMessageReader {...inbox.props} />, { wrapper: inbox.wrapper });
    await screen.findByRole("button", { name: /renewal-2\.pdf/ });
    const read = inbox.client.getQueryCache().findAll().find((entry) =>
      (entry.meta?.angeeRelatedModels as string[] | undefined)?.includes("messaging.Thread")
      && (entry.meta?.angeeRecords as { model: string; id: string }[] | undefined)?.some((record) => record.id === "message-1"));
    expect(read?.meta?.angeeBroadModels).toContain(model);
    expect(authoredQueryReadsLiveChange(read?.meta, { model, id: "new-child" })).toBe(true);
  },
);

test("a new reply naming the selected parent refreshes the reader", async () => {
  const inbox = fixture();
  render(<InboxMessageReader {...inbox.props} />, { wrapper: inbox.wrapper });
  await screen.findByRole("button", { name: /renewal-2\.pdf/ });
  const read = inbox.client.getQueryCache().findAll().find((entry) =>
    (entry.meta?.angeeRelatedModels as string[] | undefined)?.includes("messaging.Thread")
    && (entry.meta?.angeeRecords as { model: string; id: string }[] | undefined)?.some((record) => record.id === "message-1"));
  expect(authoredQueryReadsLiveChange(read?.meta, {
    model: "messaging.Message", id: "new-reply",
    relatedRecords: [{ model: "messaging.Message", id: "message-1" }],
  })).toBe(true);
});

test("a selected copy names its survivor for live refresh", async () => {
  const inbox = fixture([], false, false, "survivor-1");
  render(<InboxMessageReader {...inbox.props} />, { wrapper: inbox.wrapper });
  await screen.findByRole("button", { name: /renewal-2\.pdf/ });
  const read = inbox.client.getQueryCache().findAll().find((entry) =>
    (entry.meta?.angeeRelatedModels as string[] | undefined)?.includes("messaging.Thread")
    && (entry.meta?.angeeRecords as { model: string; id: string }[] | undefined)?.some((record) => record.id === "message-1"));
  expect(read?.meta?.angeeRecords).toEqual(expect.arrayContaining([
    { model: "messaging.Message", id: "survivor-1" },
  ]));
  expect(authoredQueryReadsLiveChange(read?.meta, { model: "messaging.Message", id: "survivor-1" })).toBe(true);
});

test("a new use refreshes the lazy Links counts", async () => {
  const inbox = fixture([{ part_id: "part-1", target: "file:file-1", count: 2 }]);
  render(<InboxMessageReader {...inbox.props} />, { wrapper: inbox.wrapper });
  await screen.findByRole("button", { name: /renewal-2\.pdf/ });
  fireEvent.click(screen.getByRole("button", { name: "Show message links" }));
  await screen.findByRole("button", { name: /accessible message uses/ });
  const uses = inbox.client.getQueryCache().findAll().find((entry) =>
    (entry.meta?.angeeBroadModels as string[] | undefined)?.includes("messaging.Part")
    && !(entry.meta?.angeeRelatedModels as string[] | undefined)?.includes("messaging.Thread"));
  expect(authoredQueryReadsLiveChange(uses?.meta, { model: "messaging.Part", id: "new-use" })).toBe(true);
});

test("Files backlinks count selects only message_count without inbox rows", async () => {
  const inbox = fixture([], true);
  function Count() {
    return <span>Related messages: {useFileMessageCount(fileContext)}</span>;
  }
  const root = createRootRoute({ component: Count });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<ShellPageTestProviders><ToastProvider><RouterProvider router={router} /></ToastProvider></ShellPageTestProviders>, { wrapper: inbox.wrapper });
  await screen.findByText("Related messages: 1");
  expect(inbox.custom.mock.calls.some(([request]) => request.meta?.gqlQuery === InboxConnectionsCount)).toBe(true);
  expect(inbox.custom.mock.calls.some(([request]) => request.meta?.gqlQuery === InboxConnections)).toBe(false);
});

test("Files backlinks count readable message uses and link to their exact part with the file pinned", async () => {
  const inbox = fixture([], true);
  const context = fileContext;
  function Messages() {
    const count = useFileMessageCount(context);
    return <><span>Related messages: {count}</span><FileMessages context={context} /></>;
  }
  const root = createRootRoute({ component: Messages });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<ShellPageTestProviders runtime={{ routeHref: createRouteHref([{ name: "nexus.inbox", path: "/nexus/inbox" }]) }}>
    <ToastProvider><RouterProvider router={router} /></ToastProvider>
  </ShellPageTestProviders>, { wrapper: inbox.wrapper });
  await screen.findByText("Related messages: 1");
  const link = await screen.findByRole("link", { name: /Renewal/ });
  const target = new URL(link.getAttribute("href")!, "https://example.test");
  expect(target.pathname).toBe("/nexus/inbox");
  expect(Object.fromEntries(target.searchParams)).toEqual({ message: "message-1", part: "part-2", related: "file:file-2", relatedFrom: "message-1" });
  expect(inbox.custom.mock.calls.some(([request]) => request.meta?.gqlQuery === InboxConnectionsCount
    && request.meta?.gqlVariables?.target === "file:file-2")).toBe(true);
  expect(inbox.custom.mock.calls.filter(([request]) => request.meta?.gqlQuery === InboxConnections)
    .every(([request]) => request.meta?.gqlVariables?.target === "file:file-2")).toBe(true);
});


test("a status reader names the author and connected account without an unrelated conversation", async () => {
  const inbox = fixture([], false, true);
  render(<InboxMessageReader {...inbox.props} />, { wrapper: inbox.wrapper });
  expect(await screen.findByRole("heading", { name: "Status update by Ada" })).toBeTruthy();
  expect(screen.queryByText("Renewals")).toBeNull();
  expect(screen.getAllByText(/status update.*Account: Connected account/).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "View status updates" }));
  expect(inbox.navigation.showUpdates).toHaveBeenCalled();
});


test("status backlinks retain provenance and never offer to open the shared feed as a conversation", async () => {
  const inbox = fixture([], true, true);
  render(<ShellPageTestProviders><ToastProvider><InboxRelatedPane navigation={inbox.props.navigation} /></ToastProvider></ShellPageTestProviders>, { wrapper: inbox.wrapper });
  const source = await screen.findByRole("button", { name: /Ada.*status update.*Original source/ });
  expect(screen.queryByRole("button", { name: "Open conversation" })).toBeNull();
  fireEvent.click(source);
  expect(inbox.navigation.read).toHaveBeenCalledWith("message-1", "part-2");
});
