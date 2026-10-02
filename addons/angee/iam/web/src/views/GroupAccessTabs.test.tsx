// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import type { FormSubmitResult, RecordPanelContext } from "@angee/ui";
import { beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  add: vi.fn(),
  listProps: [] as Record<string, unknown>[],
  mutationProps: null as Record<string, unknown> | null,
  queryData: undefined as unknown,
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => ({ data: mocks.queryData, isFetching: false, error: null }),
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  Button: ({ children }: { children?: ReactNode }) => <button type="button">{children}</button>,
  Code: ({ children }: { children?: ReactNode }) => <code>{children}</code>,
  MutationDialog: (props: Record<string, unknown>) => {
    mocks.mutationProps = props;
    return null;
  },
  RowsListView: (props: Record<string, unknown>) => {
    mocks.listProps.push(props);
    return <>{props.toolbarActions as ReactNode}</>;
  },
  SubjectPicker: () => null,
  useAuthoredResourceMutation: () => [mocks.add],
}));

import { GroupMembersTab } from "./GroupAccessTabs";

function recordPanelContext(recordId: string): RecordPanelContext {
  return {
    recordId,
    reload: vi.fn(),
    form: {} as RecordPanelContext["form"],
    focusField: vi.fn(),
  };
}

describe("group access tabs", () => {
  beforeEach(() => {
    mocks.add.mockReset();
    mocks.listProps = [];
    mocks.mutationProps = null;
    mocks.queryData = {
      groups_by_pk: {
        id: "igr_1",
        members: [{
          id: "member-1",
          subject: "auth/user:9",
          subject_type: "auth/user",
          subject_id: "9",
          label: "Service robot",
          caveat_name: "office-hours",
        }],
        bindings: [],
      },
    };
  });

  test("removes the exact canonical member tuple", () => {
    render(<GroupMembersTab {...recordPanelContext("igr_1")} />);
    const [remove] = mocks.listProps[0]?.rowActions as Array<{
      variables: (row: Record<string, string>) => unknown;
    }>;
    if (!remove) throw new Error("expected the remove-member action");
    expect(remove.variables((mocks.queryData as {
      groups_by_pk: { members: Record<string, string>[] };
    }).groups_by_pk.members[0]!)).toEqual({
      group_id: "igr_1",
      subject: "auth/user:9",
      caveat_name: "office-hours",
    });
  });

  test("adds the selected canonical subject with an explicit empty caveat", async () => {
    mocks.add.mockResolvedValue({ add_group_member: true });
    render(<GroupMembersTab {...recordPanelContext("igr_1")} />);
    const submit = mocks.mutationProps?.onSubmit as (values: { subject: string }) => Promise<FormSubmitResult<unknown>>;
    await expect(submit({ subject: "auth/user:9" })).resolves.toEqual({
      status: "ok", data: { add_group_member: true },
    });
    expect(mocks.add).toHaveBeenCalledWith({
      group_id: "igr_1",
      subject: "auth/user:9",
      caveat_name: "",
    });
  });

  test("keeps a refused member addition available as form validation", async () => {
    mocks.add.mockResolvedValue({ add_group_member: false });
    render(<GroupMembersTab {...recordPanelContext("igr_1")} />);
    const submit = mocks.mutationProps?.onSubmit as (values: { subject: string }) => Promise<FormSubmitResult<unknown>>;
    await expect(submit({ subject: "auth/user:9" })).resolves.toEqual({
      status: "invalid", issues: { fieldErrors: {}, formErrors: ["Could not add member."] },
    });
  });
});
