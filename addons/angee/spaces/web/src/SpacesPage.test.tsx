// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

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
  formReturning: undefined as readonly string[] | undefined,
  transcriptThreadIds: [] as string[],
  mutationDialogs: [] as Record<string, unknown>[],
  mutationHookCalls: 0,
  mutations: [vi.fn(), vi.fn(), vi.fn()],
  dialogRoleValue: undefined as string | undefined,
  dialogPolicyValue: undefined as string | undefined,
  groupRecord: null as Record<string, unknown> | null,
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
  Form: ({ children, returning }: { children?: React.ReactNode; returning?: readonly string[] }) => {
    pageMocks.formReturning = returning;
    return <section>{children}</section>;
  },
  Group: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  List: ({ children }: { children?: React.ReactNode }) => <section>{children}</section>,
  ListView: (props: Record<string, unknown>) => {
    pageMocks.listViews.push(props);
    return <>{props.toolbarActions as React.ReactNode}</>;
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
    values: (props) => props.title === "group.roster.notifications"
      ? { policy: pageMocks.dialogPolicyValue ?? props.initialValues?.policy }
      : { role: pageMocks.dialogRoleValue ?? props.initialValues?.role, party: "party_1" },
    submitLabel: (props) => `Submit ${String(props.title)}`,
  }),
  defineRowAction: (declaration: Record<string, unknown>) => declaration,
  rowIdVariables: (row: { id: string }) => ({ id: row.id }),
  useAuthoredResourceMutation: () => {
    const mutation = pageMocks.mutations[pageMocks.mutationHookCalls % 3]!;
    pageMocks.mutationHookCalls += 1;
    return [mutation, { fetching: false, error: null }];
  },
  useEnumOptions: (_resource: string, field: string) => (
    field === "role" ? ["OWNER", "MODERATOR", "MEMBER", "VIEWER"] : ["INBOX", "EMAIL", "MUTED"]
  ).map((value) => ({ value, label: value })),
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

function recordContext(recordId = "grp_1") {
  return { recordId, form: { displayRecord: pageMocks.groupRecord } };
}

function renderRoster() {
  render(<SpacesPage />);
  const tabs = pageMocks.resourceProps?.recordTabs as Array<{
    id: string;
    render: (context: ReturnType<typeof recordContext>) => React.ReactNode;
  }>;
  const roster = tabs.find((tab) => tab.id === "roster")!;
  const view = render(<>{roster.render(recordContext())}</>);
  return { ...view, roster };
}

describe("SpacesPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    pageMocks.resourceProps = null;
    pageMocks.resourceLists = [];
    pageMocks.listViews = [];
    pageMocks.columnFields = [];
    pageMocks.formReturning = undefined;
    pageMocks.transcriptThreadIds = [];
    pageMocks.mutationDialogs = [];
    pageMocks.mutationHookCalls = 0;
    pageMocks.dialogRoleValue = undefined;
    pageMocks.dialogPolicyValue = undefined;
    pageMocks.groupRecord = { permissions: ["write", "manage_roster"], membership_roles: ["OWNER", "MODERATOR", "MEMBER", "VIEWER"] };
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
    expect(pageMocks.formReturning).toEqual(["permissions", "membership_roles"]);

    const tabs = pageMocks.resourceProps?.recordTabs as Array<{
      id: string;
      render: (context: ReturnType<typeof recordContext>) => React.ReactNode;
    }>;
    render(<>{tabs.find((tab) => tab.id === "roster")?.render(recordContext())}</>);
    const threads = tabs.find((tab) => tab.id === "threads");
    const threadView = render(<>{threads?.render(recordContext())}</>);

    expect(pageMocks.listViews[0]).toMatchObject({
      resource: "spaces.Membership",
      scope: "local",
      baseFilter: { group: { exact: "grp_1" } },
    });
    const rosterActions = pageMocks.listViews[0]?.rowActions as Array<Record<string, unknown>>;
    expect(rosterActions).toHaveLength(3);
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
    threadView.rerender(<>{threads?.render(recordContext("grp_2"))}</>);
    await waitFor(() => expect(pageMocks.transcriptThreadIds.at(-1)).toBe("thr_3"));

    pageMocks.threadRows = [];
    threadView.rerender(<>{threads?.render(recordContext("grp_2"))}</>);
    expect(await screen.findByText("group.threads.empty")).toBeTruthy();
  });

  test("changes a roster role through the dialog using MEMBER default and lowercase wire casing", async () => {
    renderRoster();
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

  test.each([
    ["owner", ["OWNER", "MODERATOR", "MEMBER", "VIEWER"]],
    ["moderator", ["MEMBER", "VIEWER"]],
    ["reader", []],
    ["unavailable", undefined],
    ["server-restricted", ["VIEWER"]],
  ])("offers only the server-returned roles for %s", (_seat, roles) => {
    pageMocks.groupRecord = { membership_roles: roles };
    renderRoster();
    const dialog = pageMocks.mutationDialogs.find((entry) => entry.title === "group.roster.add");
    const fields = dialog?.fields as Array<{ name: string; options?: Array<{ value: string }> }>;
    expect(fields.find((field) => field.name === "role")?.options?.map((option) => option.value)).toEqual(roles ?? []);
    expect(screen.queryByRole("button", { name: "group.roster.add" }) !== null).toBe((roles?.length ?? 0) > 0);
  });

  test.each([
    ["owner on another row", ["write", "write__role", "delete"], ["change-membership-role", "remove-membership"]],
    ["moderator on a member", ["write", "delete"], ["remove-membership"]],
    ["moderator on a senior row", [], []],
    ["holder on their own row", ["set_notifications"], ["set-membership-notifications"]],
    ["pending or dismissed holder", [], []],
    ["missing permissions", undefined, []],
  ])("uses row permissions for %s", (_seat, permissions, expected) => {
    renderRoster();
    const list = pageMocks.listViews.at(-1)!;
    expect(list.fields).toEqual(expect.arrayContaining(["permissions", "notification_policy", "subtype_keys"]));
    const actions = list.rowActions as Array<{
      id: string;
      visible: (row: Record<string, unknown>) => boolean;
    }>;
    expect(actions.filter((action) => action.visible({ id: "mem_1", permissions })).map((action) => action.id)).toEqual(expected);
  });

  test.each(["INBOX", "EMAIL", "MUTED"])("submits %s through the notification action and preserves subtype preferences", async (policy) => {
    renderRoster();
    const actions = pageMocks.listViews.at(-1)?.rowActions as Array<{
      id: string;
      onSelect?: (row: Record<string, unknown>) => void;
    }>;
    act(() => actions.find((action) => action.id === "set-membership-notifications")?.onSelect?.({
      id: "mem_self",
      permissions: ["set_notifications"],
      notification_policy: "EMAIL",
      subtype_keys: ["comment"],
    }));
    const dialog = pageMocks.mutationDialogs.find((entry) => entry.open && entry.title === "group.roster.notifications");
    expect(dialog?.initialValues).toEqual({ policy: "EMAIL" });
    expect(dialog?.fields).toEqual([expect.objectContaining({ name: "policy", widget: "select", required: true })]);
    pageMocks.dialogPolicyValue = policy;
    fireEvent.click(screen.getByRole("button", { name: "Submit group.roster.notifications" }));
    await waitFor(() => expect(pageMocks.mutations[2]).toHaveBeenCalledWith({
      id: "mem_self", policy, subtype_keys: ["comment"],
    }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Submit group.roster.notifications" })).toBeNull());
  });

  test("drops an open roster dialog when navigating to a different group", () => {
    const { roster, rerender } = renderRoster();
    fireEvent.click(screen.getByRole("button", { name: "group.roster.add" }));
    expect(screen.getByRole("button", { name: "Submit group.roster.add" })).toBeTruthy();
    rerender(<>{roster.render(recordContext("grp_2"))}</>);
    expect(screen.queryByRole("button", { name: "Submit group.roster.add" })).toBeNull();
  });
});
