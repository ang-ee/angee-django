// @vitest-environment happy-dom
import { ShellPageTestProviders } from "@angee/app/testing";
import { parseFlatSearch, stringifyFlatSearch } from "@angee/app";
import { ToastProvider, useUnsavedChangesNavigationGuard, type UnsavedChangesMove } from "@angee/ui";
import { enUiMessages as UI } from "@angee/ui/i18n/en";
import { createUiTestProviders } from "@angee/ui/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { createBrowserHistory, createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { InboxConversation } from "./Conversation";
import { InboxTranscriptMatches } from "./documents";
import { conversationLeaves, inboxQuery, useInboxNavigation, type InboxNavigation } from "./state";

// The transcript's window is driven by the route's anchor, so a test moves it by navigating.
// With `typing`, the stand-in holds an unsent reply as messaging's reply box does: the guard, with the leave
// rule the inbox passes, so a test proves which of the inbox's moves ask.
const transcript = vi.hoisted(() => ({ reply: undefined as unknown, typing: false }));
vi.mock("@angee/messaging", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/messaging")>(),
  ThreadTranscript: ({ anchor, renderWindow, reply }: {
    anchor: string;
    renderWindow: (messages: { id: string }[], children: ReactElement) => ReactElement;
    reply?: { leaves: (move: UnsavedChangesMove) => boolean };
  }) => {
    transcript.reply = reply;
    return renderWindow([{ id: anchor.startsWith("message:") ? anchor.slice(8) : "m1" }],
      <div>{reply && transcript.typing ? <UnsentReply leaves={reply.leaves} /> : null}</div>);
  },
}));

function UnsentReply({ leaves }: { leaves: (move: UnsavedChangesMove) => boolean }) {
  useUnsavedChangesNavigationGuard({ isDirty: true, isDirtyNow: () => true, readOnly: false, leaves });
  return null;
}

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://nexus-conversation", providerNames: [],
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } } });
afterEach(() => { cleanup(); clearClients(); transcript.typing = false; });

const COUNT = "Full conversation · 5 messages · 2 match your filters";

/** `browser`: the window's own history, whose Back the router's blocker weighs as a browser's (a memory history's Back is never weighed). */
function fixture(initialEntries = ["/?thread=thread-one"], browser = false) {
  let navigation: InboxNavigation | undefined;
  const dataProvider = { custom: vi.fn(async ({ meta }) => {
    const variables = meta?.gqlVariables ?? {};
    if (meta?.gqlQuery !== InboxTranscriptMatches) throw new Error("Unexpected inbox operation");
    // Only the first window of the first conversation settles; every later read stays pending.
    if (variables.thread !== "thread-one" || variables.at !== "m1") return new Promise<never>(() => undefined);
    return { data: { inbox_transcript_matches: {
      thread: { id: "thread-one", conversation_label: "Ada", modality: "DIRECT" },
      count: 2, total: 5, visible: ["m1"], previous: null, next: null,
    } } };
  }) } satisfies RefineTestDataProvider;
  const scope = { ...inboxQuery({}, "UTC"), sender: "", circle: "" };
  function Pane() {
    navigation = useInboxNavigation();
    return <InboxConversation navigation={navigation} scope={scope} />;
  }
  const router = createRouter({ routeTree: createRootRoute({ component: Pane }), parseSearch: parseFlatSearch,
    stringifySearch: stringifyFlatSearch, history: browser ? createBrowserHistory()
      : createMemoryHistory({ initialEntries, initialIndex: initialEntries.length - 1 }) });
  render(<Provider dataProvider={dataProvider}><ShellPageTestProviders><ToastProvider>
    <RouterProvider router={router} />
  </ToastProvider></ShellPageTestProviders></Provider>);
  return Object.assign(router, { navigation: () => navigation! });
}

const pendingCount = () => screen.queryAllByRole("status").some((status) => status.textContent === "Loading inbox…");

test("a new transcript window keeps the conversation count; another conversation never shows it", async () => {
  const router = fixture();
  expect(await screen.findByText(COUNT)).toBeTruthy();

  // The window moves within the conversation: its read is pending, the count stays.
  await act(async () => router.navigate({ to: "/", search: (current: Record<string, unknown>) => ({ ...current, at: "message:m2" }) }));
  expect(screen.getByText(COUNT)).toBeTruthy();
  expect(pendingCount()).toBe(false);

  // Another conversation: the retained read names thread-one, so its count is withheld for a skeleton.
  await act(async () => router.navigate({ to: "/", search: { thread: "thread-two" } }));
  await waitFor(() => expect(screen.queryByText(COUNT)).toBeNull());
  expect(pendingCount()).toBe(true);
});

test("the conversation offers Reply, and only leaving it asks about an unsent reply", async () => {
  fixture();
  await screen.findByText(COUNT);
  expect(transcript.reply).toEqual({ leaves: conversationLeaves });
  const at = (search: Record<string, string>) => ({ pathname: "/nexus/inbox", search });
  const move = (current: Record<string, string>, next: Record<string, string>, pathname = "/nexus/inbox") =>
    conversationLeaves({ current: at(current), next: { ...at(next), pathname } } as never);
  // A move of the window keeps the box; another conversation, a message's reader or another page leaves it.
  expect(move({ thread: "thread-one" }, { thread: "thread-one", at: "message:m2" })).toBe(false);
  expect(move({ thread: "thread-one", at: "message:m2" }, { thread: "thread-one" })).toBe(false);
  expect(move({ thread: "thread-one" }, { thread: "thread-two" })).toBe(true);
  expect(move({ thread: "thread-one" }, { thread: "thread-one", message: "m1" })).toBe(true);
  expect(move({ thread: "thread-one" }, { thread: "thread-one" }, "/nexus/graph")).toBe(true);
});

/** The conversation opened from the results, by a push the router records, then a reply typed or not. */
async function conversationFromResults(typing: boolean) {
  window.history.replaceState(null, "", "/");
  const router = fixture([], true);
  await act(async () => router.navigate({ to: "/", search: { thread: "thread-one" } }));
  await screen.findByText(COUNT);
  transcript.typing = typing;
  // A window move keeps the conversation and renders the stand-in with what it holds now.
  await act(async () => router.navigate({ to: "/", search: (current: Record<string, unknown>) => ({ ...current, at: "message:m1" }), replace: true }));
  return router;
}

test.each([
  ["a filter change", (router: ReturnType<typeof fixture>) => { act(() => router.navigation().select("party:ada")); }],
  ["Back to the results", (router: ReturnType<typeof fixture>) => { act(() => router.history.back()); }],
])("%s leaves the conversation: it asks with a reply in progress, and goes without one", async (_move, leave) => {
  let router = await conversationFromResults(true);
  leave(router);
  fireEvent.click(await screen.findByRole("button", { name: UI["form.stay"] }));
  await waitFor(() => expect(screen.queryByRole("button", { name: UI["form.stay"] })).toBeNull());
  expect(router.state.location.search).toMatchObject({ thread: "thread-one" });
  cleanup();
  transcript.typing = false;
  router = await conversationFromResults(false);
  leave(router);
  await waitFor(() => expect(router.state.location.search).not.toHaveProperty("thread"));
  expect(screen.queryByRole("button", { name: UI["form.stay"] })).toBeNull();
});
