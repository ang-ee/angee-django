// @vitest-environment happy-dom
import { ShellPageTestProviders } from "@angee/app/testing";
import { ToastProvider, createRouteHref, type ChatterViewContext } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import type { ReactNode } from "react";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("@angee/app", async (original) => ({
  ...(await original<typeof import("@angee/app")>()),
  useAuth: () => ({ user: { id: "actor" } }),
}));

import { InboxConversation } from "./Conversation";
import { InboxConnections, InboxMessage, InboxRelatedTarget, InboxTranscriptMatches } from "./documents";
import { InboxMessageReader } from "./MessageReader";
import { InboxRelatedPane } from "./Related";
import { FileMessages, useFileMessageCount } from "./FileMessages";
import type { InboxNavigation, InboxScope } from "./state";

const { Provider, createClient, clearClients } = createUiTestProviders({ apiUrl: "test://inbox-files", providerNames: [] });
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
afterEach(() => { cleanup(); clearClients(); vi.restoreAllMocks(); });

const pdf = (n: number) => ({
  id: `part-${n}`, name: `renewal-${n}.pdf`, type: "application/pdf", position: n, role: "BODY",
  disposition: "ATTACHMENT", cid: "", parent: null, fragment: null,
  file: {
    id: `file-${n}`, filename: `renewal-${n}.pdf`, title: "", size_bytes: 2048, url: `/storage/download/token-${n}/renewal-${n}.pdf`,
    mime_type: { mime_type: "application/pdf", label: "PDF" },
  },
});
const sender = { id: "handle-1", display_name: "Ada", value: "ada@example.com", party_link_confirmed: false, avatar: null, party: null };
const message = {
  id: "message-1", feed_order_key: "v1:0001", direction: "INBOUND", preview: "Both renewals attached", title: "Renewal",
  message_type: "EMAIL", platform: "email", status: "RECEIVED", starred: false, sent_at: "2026-10-01T10:00:00Z",
  created_at: "2026-10-01T10:00:00Z", received_at: "2026-10-01T10:00:00Z", external_id: "", sender,
  thread: { id: "thread-1", title: null, modality: "EMAIL", conversation_label: "Renewals" }, channel: null,
  participants: [], parts: [pdf(1), pdf(2)], reaction_groups: [],
};

function fixture(uses: { part_id: string; target: string; count: number }[] = [], fileUses = false) {
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const dataProvider = { custom: vi.fn(async ({ meta }) => {
    if (meta?.gqlQuery === InboxTranscriptMatches) {
      return { data: { inbox_transcript_matches: {
        thread: { id: "thread-1", conversation_label: "Renewals" }, count: 0, total: 1, visible: [], previous: null, next: null,
      } } };
    }
    if (meta?.gqlQuery === InboxMessage) {
      return { data: { inbox_message: { matches: true, uses, message } } };
    }
    if (meta?.gqlQuery === InboxRelatedTarget) {
      const part = pdf(2);
      return { data: { inbox_related_target: {
        id: "file:file-2", kind: "file", label: part.name, count: 1, unit: "messages",
        source: message, part, file: part.file, fragment: null,
      } } };
    }
    if (meta?.gqlQuery === InboxConnections) {
      return { data: { inbox_connections: { rows: fileUses ? [{ id: "use-1", message, part: pdf(2) }] : [], count: fileUses ? 1 : 0, message_count: fileUses ? 1 : 0 } } };
    }
    return { data: { thread_message_feed: {
      messages: [message], count: 1, older_cursor: null, has_older: false, has_more_in_window: false,
      has_older_than_through: false, newer_cursor: null, has_newer: false, has_newer_than_before: false,
    } } };
  }) } satisfies RefineTestDataProvider;
  function Providers({ children }: { children: ReactNode }) {
    return <Provider dataProvider={dataProvider} queryClient={client} liveProvider={{ subscribe: vi.fn(() => "inbox-files"), unsubscribe: vi.fn() }}
      authProvider={{ login: async () => ({ success: true }), logout: async () => ({ success: true }), check: async () => ({ authenticated: true }), onError: async () => ({}) }}
    >{children}</Provider>;
  }
  const navigation = {
    thread: "thread-1", at: "", message: "message-1", part: "", related: "file:file-2", relatedFrom: "message-1",
    patch: vi.fn(), previewFile: vi.fn(), read: vi.fn(), results: vi.fn(), conversation: vi.fn(), connect: vi.fn(),
    href: (values: Record<string, string | undefined>) => createRouteHref([{ name: "inbox", path: "/nexus/inbox" }])("inbox", {}, values),
  };
  const scope = { coverage: { timezone: "UTC" }, search: {}, sender: "", circle: "" };
  return {
    custom: dataProvider.custom,
    wrapper: Providers,
    navigation,
    props: { navigation: navigation as unknown as InboxNavigation, scope: scope as unknown as InboxScope },
  };
}

test("an attachment in the conversation previews in Related", async () => {
  const inbox = fixture();
  render(<InboxConversation {...inbox.props} />, { wrapper: inbox.wrapper });

  fireEvent.click(await screen.findByRole("button", { name: /renewal-2\.pdf/ }));

  expect(inbox.navigation.previewFile).toHaveBeenCalledWith("file-2", "message-1");
  expect(inbox.navigation.patch).not.toHaveBeenCalled();
});

test("an attachment in the reader previews in Related and leaves the message full width", async () => {
  const inbox = fixture();
  render(<InboxMessageReader {...inbox.props} />, { wrapper: inbox.wrapper });

  fireEvent.click(await screen.findByRole("button", { name: /renewal-2\.pdf/ }));

  expect(inbox.navigation.previewFile).toHaveBeenCalledWith("file-2", "message-1");
  expect(inbox.navigation.patch).not.toHaveBeenCalled();
  expect(screen.queryByRole("complementary", { name: "Attachment preview" })).toBeNull();
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
  const use = await screen.findByRole("button", { name: "renewal-2.pdf 2 accessible message uses" });
  expect(screen.getAllByRole("button", { name: /accessible message uses/ })).toHaveLength(1);
  fireEvent.click(use);
  expect(inbox.navigation.connect).toHaveBeenCalledWith("file:file-2", "message-1");
});

test("Files backlinks count readable message uses and link to their exact part with the file pinned", async () => {
  const inbox = fixture([], true);
  const context: ChatterViewContext = {
    pathname: "/storage/file-2", params: { id: "file-2" },
    route: { name: "storage.file", path: "/storage/$id", viewType: "storage/file", modelLabel: "storage.File" },
    view: { kind: "record", type: "storage/file", sqid: "file-2" },
  };
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
  expect(inbox.custom.mock.calls.filter(([request]) => request.meta?.gqlQuery === InboxConnections)
    .every(([request]) => request.meta?.gqlVariables?.target === "file:file-2")).toBe(true);
});
