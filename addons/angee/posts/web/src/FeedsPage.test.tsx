// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

type MockPart = {
  fragment?: { text?: string | null } | null;
  file?: { url?: string | null } | null;
};

const pageMocks = vi.hoisted(() => ({
  resourceProps: null as Record<string, unknown> | null,
  listProps: null as Record<string, unknown> | null,
  listViews: [] as Record<string, unknown>[],
  columnFields: [] as string[],
  authoredCalls: [] as Array<{
    variables: Record<string, unknown> | undefined;
    options: Record<string, unknown> | undefined;
  }>,
  useAuthoredQuery: vi.fn(),
}));

vi.mock("@angee/refine", async () => {
  const actual = await vi.importActual<typeof import("@angee/refine")>("@angee/refine");
  return {
    ...actual,
    useAuthoredQuery: pageMocks.useAuthoredQuery,
  };
});

vi.mock("@angee/ui", async () => {
  const actual = await vi.importActual<typeof import("@angee/ui")>("@angee/ui");
  return {
    Avatar: ({ initials }: { initials?: string }) => <span>{initials}</span>,
    Column: ({ field }: { field: string }) => {
      pageMocks.columnFields.push(field);
      return null;
    },
    EmptyState: ({ title }: { title: React.ReactNode }) => <section>{title}</section>,
    ErrorBanner: ({ title }: { title?: React.ReactNode }) => <section>{title}</section>,
    Field: () => null,
    Form: ({ children }: { children?: React.ReactNode }) => <section>{children}</section>,
    Group: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
    List: (props: Record<string, unknown>) => {
      pageMocks.listProps = props;
      return <section>{props.children as React.ReactNode}</section>;
    },
    ListView: (props: Record<string, unknown>) => {
      pageMocks.listViews.push(props);
      return <section>{String(props.emptyContent ?? "")}</section>;
    },
    LoadingPanel: ({ message }: { message?: string }) => <section>{message}</section>,
    MessageFeed: ({ children, label }: { children?: React.ReactNode; label?: string }) => (
      <ul aria-label={label}>{children}</ul>
    ),
    MessagePartsView: ({ parts }: { parts: readonly MockPart[] }) => (
      <div>{parts.map((part) => part.fragment?.text ?? "").join("")}</div>
    ),
    MessageRow: ({
      author,
      children,
      reactions,
    }: {
      author?: React.ReactNode;
      children?: React.ReactNode;
      reactions?: React.ReactNode;
    }) => (
      <li>
        <span>{author}</span>
        {children}
        {reactions}
      </li>
    ),
    ReactionBar: ({ reactions }: { reactions: readonly { reaction: string; count: number }[] }) => (
      <div>{reactions.map((reaction) => `${reaction.reaction}:${reaction.count}`).join(",")}</div>
    ),
    reactionsFromGroups: actual.reactionsFromGroups,
    ResourceList: (props: Record<string, unknown>) => {
      pageMocks.resourceProps = props;
      return <div>{props.children as React.ReactNode}</div>;
    },
    avatarInitials: (label: string) => label.slice(0, 2).toUpperCase(),
    createNamespaceT: () => () => (key: string) => key,
    errorMessage: (error: unknown, fallback: string) =>
      error instanceof Error ? error.message : fallback,
  };
});

vi.mock("./i18n", () => ({
  usePostsT: () => (key: string) => key,
}));

import { FeedsPage } from "./FeedsPage";

function feedMessage(): Record<string, unknown> {
  return {
    id: "msg_1",
    preview: "fallback",
    sent_at: "2026-08-01T10:00:00Z",
    created_at: "2026-08-01T10:00:00Z",
    sender: {
      display_name: "Ada Envelope",
      value: "ada@example.com",
      party_link_confirmed: true,
      party: { display_name: "Ada Curated" },
    },
    parts: [{ fragment: { text: "Post body" }, file: null }],
    reaction_groups: [
      {
        reaction: "like",
        count: 3,
        self_reacted: false,
        handles: [{ display_name: "Ada", value: "ada@example.com" }],
      },
    ],
  };
}

describe("FeedsPage", () => {
  beforeEach(() => {
    pageMocks.resourceProps = null;
    pageMocks.listProps = null;
    pageMocks.listViews = [];
    pageMocks.columnFields = [];
    pageMocks.authoredCalls = [];
    pageMocks.useAuthoredQuery.mockReset();
    pageMocks.useAuthoredQuery.mockImplementation(
      (_document: unknown, variables?: Record<string, unknown>, options?: Record<string, unknown>) => {
        pageMocks.authoredCalls.push({ variables, options });
        return {
          data: {
            messages: [feedMessage()],
          },
          fetching: false,
          error: null,
          refetch: vi.fn(),
        };
      },
    );
  });

  test("composes the feeds resource list and detail tabs", () => {
    render(<FeedsPage />);

    expect(pageMocks.resourceProps).toMatchObject({
      resource: "posts.Feed",
      placement: "inline",
      routed: true,
      hideCreate: true,
    });
    expect(pageMocks.listProps).toMatchObject({ resource: "posts.Feed" });
    expect(pageMocks.columnFields).toEqual(
      expect.arrayContaining([
        "display_name",
        "backend_class",
        "handle.display_name",
        "lifecycle",
        "runtime_status",
        "last_sync_status",
        "last_sync_completed_at",
      ]),
    );

    const tabs = pageMocks.resourceProps?.recordTabs as Array<{
      id: string;
      render: (context: { recordId: string }) => React.ReactNode;
    }>;
    expect(tabs.map((tab) => tab.id)).toEqual(["posts", "follows"]);
  });

  test("renders feed-scoped posts through shared message primitives", () => {
    render(<FeedsPage />);
    const tabs = pageMocks.resourceProps?.recordTabs as Array<{
      id: string;
      render: (context: { recordId: string }) => React.ReactNode;
    }>;
    const postsTab = tabs.find((tab) => tab.id === "posts");
    render(<>{postsTab?.render({ recordId: "fed_1" })}</>);

    expect(pageMocks.authoredCalls.at(-1)).toMatchObject({
      variables: { feedId: "fed_1", limit: 50 },
    });
    expect(pageMocks.authoredCalls.at(-1)?.options?.models).toEqual(
      expect.arrayContaining(["messaging.Message", "messaging.Reaction"]),
    );
    expect(screen.getByText("Ada Curated")).toBeTruthy();
    expect(screen.getByText("Post body")).toBeTruthy();
    expect(screen.getByText("like:3")).toBeTruthy();
  });

  test("scopes feed follows to the open feed", () => {
    render(<FeedsPage />);
    const tabs = pageMocks.resourceProps?.recordTabs as Array<{
      id: string;
      render: (context: { recordId: string }) => React.ReactNode;
    }>;
    const followsTab = tabs.find((tab) => tab.id === "follows");
    render(<>{followsTab?.render({ recordId: "fed_1" })}</>);

    expect(pageMocks.listViews.at(-1)).toMatchObject({
      resource: "posts.FeedFollow",
      scope: "local",
      baseFilter: { feed: { exact: "fed_1" } },
    });
  });
});
