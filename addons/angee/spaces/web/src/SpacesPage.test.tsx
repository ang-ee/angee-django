// @vitest-environment happy-dom

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

type MockThreadRow = {
  id: string;
  title?: { text?: string | null } | null;
  groups?: Array<{ id: string; name: string }>;
};

const pageMocks = vi.hoisted(() => ({
  resourceProps: null as Record<string, unknown> | null,
  resourceLists: [] as Record<string, unknown>[],
  listViews: [] as Record<string, unknown>[],
  columnFields: [] as string[],
  transcriptThreadIds: [] as string[],
  mutationDialogs: [] as Record<string, unknown>[],
  mutationHookCalls: 0,
  mutations: [vi.fn(), vi.fn()],
  dialogRoleValue: undefined as string | undefined,
  threadRows: [
    { id: "thr_1", title: { text: "Primary" }, groups: [{ id: "grp_1", name: "Community" }] },
    {
      id: "thr_2",
      title: { text: "Side thread" },
      groups: [
        { id: "grp_1", name: "Community" },
        { id: "grp_2", name: "Moderators" },
      ],
    },
  ] as MockThreadRow[],
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const original = await importOriginal<typeof import("@angee/ui")>();
  const { createMutationDialogTestDouble } = await import("@angee/ui/testing");
  return {
  ...original,
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
    return null;
  },
  ResourceList: (props: Record<string, unknown>) => {
    pageMocks.resourceLists.push(props);
    if (props.resource === "spaces.Group") pageMocks.resourceProps = props;
    const recordId = typeof props.recordId === "string" ? props.recordId : null;
    const onSelect = props.onSelect as ((id: string | null) => void) | undefined;
    React.useEffect(() => {
      if (props.resource !== "spaces.GroupThread" || !props.selectFirstRecord) return;
      if (!recordId || !pageMocks.threadRows.some((row) => row.id === recordId)) {
        onSelect?.(pageMocks.threadRows[0]?.id ?? null);
      }
    }, [onSelect, props.baseFilter, props.resource, props.selectFirstRecord, recordId]);
    if (props.resource === "spaces.GroupThread") {
      const renderRecord = props.renderRecord as
        | ((context: { recordId: string | null }) => React.ReactNode)
        | undefined;
      return (
        <div data-testid="thread-resource-list">
          {pageMocks.threadRows.map((row) => (
            <button key={row.id} type="button" onClick={() => onSelect?.(row.id)}>
              {row.title?.text ?? row.id}
            </button>
          ))}
          {renderRecord?.({ recordId })}
        </div>
      );
    }
    return <div>{props.children as React.ReactNode}</div>;
  },
  SplitPanes: ({ children }: { children?: React.ReactNode }) => <section>{children}</section>,
  SplitPane: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  SplitPaneHandle: () => <span />,
  EmptyState: ({ title }: { title: React.ReactNode }) => <section>{title}</section>,
  Button: ({
    children,
    onClick,
    disabled,
    "aria-label": ariaLabel,
    type,
  }: {
    children?: React.ReactNode;
    onClick?: React.MouseEventHandler<HTMLButtonElement>;
    disabled?: boolean;
    "aria-label"?: string;
    type?: "button" | "submit";
  }) => (
    <button type={type} aria-label={ariaLabel} disabled={disabled} onClick={onClick}>
      {children}
    </button>
  ),
  Chip: ({ children }: { children?: React.ReactNode }) => <span>{children}</span>,
  Glyph: () => null,
  MutationDialog: createMutationDialogTestDouble({
    capture: (props) => {
      pageMocks.mutationDialogs.push(props);
    },
    values: (props) => ({
      role:
        pageMocks.dialogRoleValue
        ?? (props.initialValues as Record<string, unknown> | undefined)?.role,
    }),
    submitLabel: (props) => `Submit ${String(props.title)}`,
  }),
  cn: (...classes: Array<string | false | null | undefined>) =>
    classes.filter(Boolean).join(" "),
  defineRowAction: (declaration: Record<string, unknown>) => declaration,
  rowIdVariables: (row: { id: string }) => ({ id: row.id }),
  errorMessage: (error: unknown) => String(error),
  useAuthoredResourceMutation: () => {
    const mutation = pageMocks.mutations[pageMocks.mutationHookCalls % 2]!;
    pageMocks.mutationHookCalls += 1;
    return [mutation, { fetching: false, error: null }];
  },
  useConfirm: () => vi.fn(async () => true),
  useToast: () => ({
    toast: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    danger: vi.fn(),
  }),
  };
});

vi.mock("@angee/messaging", () => ({
  ThreadTranscript: ({ threadId }: { threadId: string }) => {
    pageMocks.transcriptThreadIds.push(threadId);
    return <section data-testid="thread-transcript">{threadId}</section>;
  },
}));

vi.mock("./i18n", () => ({
  useSpacesT: () => (key: string) => key,
}));

import { SpacesPage } from "./SpacesPage";

describe("SpacesPage", () => {
  beforeEach(() => {
    pageMocks.resourceProps = null;
    pageMocks.resourceLists = [];
    pageMocks.listViews = [];
    pageMocks.columnFields = [];
    pageMocks.transcriptThreadIds = [];
    pageMocks.mutationDialogs = [];
    pageMocks.mutationHookCalls = 0;
    pageMocks.dialogRoleValue = undefined;
    pageMocks.threadRows = [
      { id: "thr_1", title: { text: "Primary" }, groups: [{ id: "grp_1", name: "Community" }] },
      { id: "thr_2", title: { text: "Side thread" }, groups: [{ id: "grp_1", name: "Community" }, { id: "grp_2", name: "Moderators" }] },
    ];
    for (const mutation of pageMocks.mutations) mutation.mockReset();
  });

  test("composes the group resource and scoped roster/thread primitives", async () => {
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
    render(<>{tabs.find((tab) => tab.id === "roster")?.render({ recordId: "grp_1" })}</>);
    const threads = tabs.find((tab) => tab.id === "threads");
    const threadView = render(<>{threads?.render({ recordId: "grp_1" })}</>);

    expect(pageMocks.listViews[0]).toMatchObject({
      resource: "spaces.Membership",
      scope: "local",
      baseFilter: { group: { exact: "grp_1" } },
    });
    const rosterActions = pageMocks.listViews[0]?.rowActions as Array<Record<string, unknown>>;
    expect(rosterActions).toHaveLength(2);
    expect(rosterActions[0]).toMatchObject({
      kind: "page",
      id: "change-membership-role",
      pendingPolicy: "disable-actions",
    });
    const remove = rosterActions[1] as {
      variables: (row: { id: string }) => unknown;
      pendingPolicy: string;
    };
    expect(remove.variables({ id: "mem_1" })).toEqual({ id: "mem_1" });
    expect(remove).toMatchObject({
      kind: "authored",
      pendingPolicy: "disable-actions",
    });
    const threadList = pageMocks.resourceLists.find(
      (props) => props.resource === "spaces.GroupThread",
    );
    expect(threadList).toMatchObject({
      resource: "spaces.GroupThread",
      scope: "local",
      placement: "split",
      hideCreate: true,
      selectFirstRecord: true,
      baseFilter: { groups: { exact: "grp_1" } },
    });
    expect(threadList?.rowHref).toBeUndefined();
    expect((await screen.findByTestId("thread-transcript")).textContent).toBe("thr_1");

    fireEvent.click(screen.getByRole("button", { name: "Side thread" }));
    expect(pageMocks.transcriptThreadIds.at(-1)).toBe("thr_2");

    pageMocks.threadRows = [{ id: "thr_3", title: { text: "Other group" }, groups: [{ id: "grp_2", name: "Moderators" }] }];
    threadView.rerender(<>{threads?.render({ recordId: "grp_2" })}</>);
    await waitFor(() => expect(pageMocks.transcriptThreadIds.at(-1)).toBe("thr_3"));

    pageMocks.threadRows = [];
    threadView.rerender(<>{threads?.render({ recordId: "grp_2" })}</>);
    expect(await screen.findByText("group.threads.empty")).toBeTruthy();
  });

  test("changes a roster role through the dialog using MEMBER default and lowercase wire casing", async () => {
    render(<SpacesPage />);
    const tabs = pageMocks.resourceProps?.recordTabs as Array<{
      id: string;
      render: (context: { recordId: string }) => React.ReactNode;
    }>;
    const roster = tabs.find((tab) => tab.id === "roster");
    render(<>{roster?.render({ recordId: "grp_1" })}</>);
    const membershipList = pageMocks.listViews.find(
      (props) => props.resource === "spaces.Membership",
    );
    const [changeRole] = membershipList?.rowActions as Array<{
      onSelect: (row: Record<string, unknown>) => void;
    }>;
    act(() => changeRole?.onSelect({ id: "mem_1" }));

    const roleDialog = pageMocks.mutationDialogs.find(
      (dialog) => dialog.open && dialog.title === "group.roster.changeRole",
    );
    expect(roleDialog?.initialValues).toEqual({ role: "MEMBER" });
    pageMocks.dialogRoleValue = "MODERATOR";
    fireEvent.click(screen.getByRole("button", {
      name: "Submit group.roster.changeRole",
    }));

    await waitFor(() =>
      expect(pageMocks.mutations[1]).toHaveBeenCalledWith({
        id: "mem_1",
        role: "moderator",
      }),
    );
  });
});
