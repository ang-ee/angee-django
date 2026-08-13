// @vitest-environment happy-dom

import { act, render } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

type MockThreadRow = {
  id: string;
  title?: { text?: string | null } | null;
};

const pageMocks = vi.hoisted(() => ({
  resourceProps: null as Record<string, unknown> | null,
  listViews: [] as Record<string, unknown>[],
  columnFields: [] as string[],
  transcriptThreadIds: [] as string[],
  threadRows: [
    { id: "thr_1", title: { text: "Primary" } },
    { id: "thr_2", title: { text: "Side thread" } },
  ] as MockThreadRow[],
}));

vi.mock("@angee/ui", () => ({
  Action: () => null,
  Column: ({ field }: { field: string }) => {
    pageMocks.columnFields.push(field);
    return null;
  },
  Field: () => null,
  Form: ({ children }: { children?: React.ReactNode }) => <section>{children}</section>,
  Group: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  List: ({ children }: { children?: React.ReactNode }) => <section>{children}</section>,
  ListView: (props: Record<string, unknown>) => {
    pageMocks.listViews.push(props);
    React.useEffect(() => {
      if (props.resource !== "spaces.GroupThread") return;
      const onListStateChange = props.onListStateChange as
        | ((state: {
          rows: readonly MockThreadRow[];
          total: number;
          page: number;
          pageSize: number;
          pageCount: number;
          hasNext: boolean;
          hasPrev: boolean;
          fetching: boolean;
        }) => void)
        | undefined;
      onListStateChange?.({
        rows: pageMocks.threadRows,
        total: pageMocks.threadRows.length,
        page: 1,
        pageSize: 10,
        pageCount: 1,
        hasNext: false,
        hasPrev: false,
        fetching: false,
      });
    }, [props.resource]);
    return null;
  },
  ResourceList: (props: Record<string, unknown>) => {
    pageMocks.resourceProps = props;
    return <div>{props.children as React.ReactNode}</div>;
  },
  SplitPanes: ({ children }: { children?: React.ReactNode }) => <section>{children}</section>,
  SplitPane: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  SplitPaneHandle: () => <span />,
  EmptyState: ({ title }: { title: React.ReactNode }) => <section>{title}</section>,
  Button: ({ children }: { children?: React.ReactNode }) => <button>{children}</button>,
  Glyph: () => null,
  MutationDialog: () => null,
  cn: (...classes: Array<string | false | null | undefined>) =>
    classes.filter(Boolean).join(" "),
  errorMessage: (error: unknown) => String(error),
  useAuthoredResourceMutation: () => [vi.fn(), { fetching: false, error: null }],
  useConfirm: () => vi.fn(async () => true),
  useToast: () => ({
    toast: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    danger: vi.fn(),
  }),
}));

vi.mock("@angee/messaging", () => ({
  ThreadTranscript: ({ threadId }: { threadId: string }) => {
    pageMocks.transcriptThreadIds.push(threadId);
    return <section data-testid="thread-transcript">{threadId}</section>;
  },
}));

vi.mock("./i18n", () => ({
  useSpacesT: () => (key: string) => key,
}));

import { SpacesPage, membershipRole, membershipRoleWireValue } from "./SpacesPage";

describe("SpacesPage", () => {
  beforeEach(() => {
    pageMocks.resourceProps = null;
    pageMocks.listViews = [];
    pageMocks.columnFields = [];
    pageMocks.transcriptThreadIds = [];
  });

  test("membership role narrows to the enum and lowercases for the _set write", () => {
    expect(membershipRole("MODERATOR")).toBe("MODERATOR");
    expect(membershipRole("bogus")).toBe("MEMBER");
    expect(membershipRoleWireValue("OWNER")).toBe("owner");
    expect(membershipRoleWireValue(undefined)).toBe("member");
  });

  test("composes the group resource and scoped roster/thread primitives", () => {
    render(<SpacesPage />);

    expect(pageMocks.resourceProps).toMatchObject({
      resource: "spaces.Group",
      placement: "inline",
      routed: true,
    });
    expect(pageMocks.columnFields).toEqual(
      expect.arrayContaining(["name", "parent.name", "visibility", "created_at"]),
    );

    const tabs = pageMocks.resourceProps?.recordTabs as Array<{
      id: string;
      render: (context: { recordId: string }) => React.ReactNode;
    }>;
    for (const tab of tabs) {
      render(<>{tab.render({ recordId: "grp_1" })}</>);
    }

    expect(pageMocks.listViews[0]).toMatchObject({
      resource: "spaces.Membership",
      scope: "local",
      baseFilter: { group: { exact: "grp_1" } },
    });
    expect(pageMocks.listViews[1]).toMatchObject({
      resource: "spaces.GroupThread",
      scope: "local",
      baseFilter: { group: { exact: "grp_1" } },
    });
    const threadList = pageMocks.listViews.find(
      (props) => props.resource === "spaces.GroupThread",
    );
    expect(threadList?.rowHref).toBeUndefined();
    expect(pageMocks.transcriptThreadIds.at(-1)).toBe("thr_1");

    const onRowClick = threadList?.onRowClick as
      | ((row: MockThreadRow) => void)
      | undefined;
    act(() => onRowClick?.(pageMocks.threadRows[1]!));
    expect(pageMocks.transcriptThreadIds.at(-1)).toBe("thr_2");
  });
});
