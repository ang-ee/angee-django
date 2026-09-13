// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  add: vi.fn(),
  listProps: [] as Record<string, unknown>[],
  mutationProps: null as Record<string, unknown> | null,
  queryData: undefined as unknown,
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: () => ({ data: mocks.queryData, isFetching: false, error: null }),
}));

vi.mock("@angee/ui", () => ({
  Button: ({ children }: { children?: ReactNode }) => <button type="button">{children}</button>,
  Code: ({ children }: { children?: ReactNode }) => <code>{children}</code>,
  MutationDialog: (props: Record<string, unknown>) => {
    mocks.mutationProps = props;
    return null;
  },
  RowsListView: (props: Record<string, unknown>) => {
    mocks.listProps.push(props);
    return null;
  },
  SubjectPicker: () => null,
  TextLink: ({ children, href }: { children?: ReactNode; href: string }) => <a href={href}>{children}</a>,
  defineRowAction: (value: Record<string, unknown>) => value,
  mutationDialogValueCodecs: {
    requiredString: (value: unknown) => String(value),
  },
  useAuthoredResourceMutation: () => [mocks.add],
  useResourceRecordHref: () => (id: string) => `/records/${id}`,
  useResourceRoute: () => "/records",
}));

import { GroupBindingsTab, GroupMembersTab } from "./GroupAccessTabs";

describe("group access tabs", () => {
  beforeEach(() => {
    mocks.add.mockReset();
    mocks.listProps = [];
    mocks.mutationProps = null;
    mocks.queryData = {
      groups_by_pk: {
        id: "grp_1",
        members: [{
          id: "member-1",
          subject: "auth/user:9",
          subject_type: "auth/user",
          subject_id: "9",
          label: "Service robot",
          caveat_name: "office-hours",
        }],
        bindings: [{
          id: "binding-1",
          resource: "projects/project:12",
          resource_type: "projects/project",
          resource_id: "12",
          relation: "viewer",
          caveat_name: "",
          target_model: "projects.Project",
          target_id: "prj_12",
        }],
      },
    };
  });

  test("removes the exact canonical member tuple", () => {
    render(<GroupMembersTab recordId="grp_1" />);
    const [remove] = mocks.listProps[0]?.rowActions as Array<{
      variables: (row: Record<string, string>) => unknown;
    }>;
    expect(remove.variables((mocks.queryData as {
      groups_by_pk: { members: Record<string, string>[] };
    }).groups_by_pk.members[0]!)).toEqual({
      group_id: "grp_1",
      subject: "auth/user:9",
      caveat_name: "office-hours",
    });
  });

  test("adds the selected canonical subject with an explicit empty caveat", async () => {
    mocks.add.mockResolvedValue({ add_group_member: true });
    render(<GroupMembersTab recordId="grp_1" />);
    const submit = mocks.mutationProps?.onSubmit as (values: { subject: string }) => Promise<void>;
    await submit({ subject: "auth/user:9" });
    expect(mocks.add).toHaveBeenCalledWith({
      group_id: "grp_1",
      subject: "auth/user:9",
      caveat_name: "",
    });
  });

  test("links a binding through its backend-projected target model and public id", () => {
    render(<GroupBindingsTab recordId="grp_1" />);
    const [resource] = mocks.listProps[0]?.columns as Array<{
      render: (row: Record<string, unknown>) => ReactNode;
    }>;
    const binding = (mocks.queryData as {
      groups_by_pk: { bindings: Record<string, unknown>[] };
    }).groups_by_pk.bindings[0]!;
    const rendered = render(<>{resource.render(binding)}</>);
    expect(rendered.getByRole("link").getAttribute("href")).toBe("/records/prj_12");
  });
});
