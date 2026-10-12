// @vitest-environment happy-dom
import { ShellPageTestProviders } from "@angee/app/testing";
import { ToastProvider } from "@angee/ui";
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
import type { InboxNavigation, InboxScope } from "./state";

const { Provider, createClient, clearClients } = createUiTestProviders({ apiUrl: "test://inbox-files", providerNames: [] });
// The transcript virtualizes its rows over the measured viewport, which happy-dom
// reports as zero-sized; give elements a viewport so the rows render.
beforeEach(() => {
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

function fixture() {
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const dataProvider = { custom: vi.fn(async ({ meta }) => {
    if (meta?.gqlQuery === InboxTranscriptMatches) {
      return { data: { inbox_transcript_matches: {
        thread: { id: "thread-1", conversation_label: "Renewals" }, count: 0, total: 1, visible: [], previous: null, next: null,
      } } };
    }
    if (meta?.gqlQuery === InboxMessage) {
      return { data: { inbox_message: { matches: true, uses: [], message } } };
    }
    if (meta?.gqlQuery === InboxRelatedTarget) {
      const part = pdf(2);
      return { data: { inbox_related_target: {
        id: "file:file-2", kind: "file", label: part.name, count: 1, unit: "messages",
        source: message, part, file: part.file, fragment: null,
      } } };
    }
    if (meta?.gqlQuery === InboxConnections) {
      return { data: { inbox_connections: { rows: [], count: 0, message_count: 0 } } };
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
    patch: vi.fn(), previewFile: vi.fn(), read: vi.fn(), results: vi.fn(), back: vi.fn(), conversation: vi.fn(), connect: vi.fn(),
  };
  const scope = { coverage: { timezone: "UTC" }, search: {}, sender: "", circle: "" };
  return {
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
});
