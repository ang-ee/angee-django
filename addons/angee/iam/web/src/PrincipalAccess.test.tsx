// @vitest-environment happy-dom

import { render, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listProps: [] as Record<string, unknown>[],
  queryOptions: null as Record<string, unknown> | null,
  queryVariables: undefined as Record<string, unknown> | undefined,
  record: { assignment_subject: "auth/user:usr_alice" } as Record<string, unknown> | null,
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: (
    _document: unknown,
    variables: Record<string, unknown> | undefined,
    options: Record<string, unknown>,
  ) => {
    mocks.queryVariables = variables;
    mocks.queryOptions = options;
    return {
      data: {
        iam_principal_access: {
          subject: "auth/user:usr_alice",
          roles: [{
            id: "knowledge/role:vault_viewer",
            role: "knowledge/role:vault_viewer",
            role_name: "Vault Viewer",
            namespace: "knowledge",
            source: "auth/group:grp_editors#member",
            source_label: "Editors",
            direct: false,
          }],
          grants: [{
            id: "grant-1",
            resource: "projects/project:prj_1",
            resource_type: "projects/project",
            resource_id: "prj_1",
            relation: "editor",
            source: "auth/group:grp_editors#member",
            source_label: "Editors",
            direct: false,
            caveat_name: "",
            target_model: "projects.Project",
            target_id: "prj_1",
          }],
          permissions: [{
            id: "permission-1",
            resource: "projects/project:prj_1",
            resource_type: "projects/project",
            resource_id: "prj_1",
            permission: "write",
            source: "projects/project:prj_1#editor",
            direct: false,
            caveat_name: "",
            target_model: "projects.Project",
            target_id: "prj_1",
          }],
        },
      },
      isFetching: false,
      error: null,
    };
  },
}));

vi.mock("@angee/ui", () => {
  const TabsRoot = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  const Tabs = Object.assign(TabsRoot, {
    List: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
    Tab: ({ children }: { children?: ReactNode }) => <button type="button">{children}</button>,
    Count: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
    Panel: ({ children }: { children?: ReactNode }) => <section>{children}</section>,
  });
  return {
    Code: ({ children }: { children?: ReactNode }) => <code>{children}</code>,
    createNamespaceT: (
      _namespace: string,
      fallback: Record<string, string>,
    ) => () => (key: string) => fallback[key] ?? key,
    InlineEmpty: ({ label }: { label: ReactNode }) => <div>{label}</div>,
    RowsListView: (props: Record<string, unknown>) => {
      mocks.listProps.push(props);
      return null;
    },
    Tabs,
    NavLink: ({ children, href }: { children?: ReactNode; href: string }) => (
      <a href={href}>{children}</a>
    ),
    useRecordChromeContext: () => ({ record: mocks.record }),
    useResourceRecordHref: () => (id: string) => `/projects/${id}`,
    useResourceRoute: () => "/projects",
  };
});

import { PrincipalAccessTab, usePrincipalAccessRecordTab } from "./PrincipalAccess";

describe("principal access tab", () => {
  beforeEach(() => {
    mocks.listProps = [];
    mocks.queryOptions = null;
    mocks.queryVariables = undefined;
    mocks.record = { assignment_subject: "auth/user:usr_alice" };
  });

  test("offers the shared record tab", () => {
    expect(renderHook(() => usePrincipalAccessRecordTab()).result.current?.id).toBe("access");
  });

  test("queries the record principal and renders roles, grants, and permission paths", () => {
    render(<PrincipalAccessTab />);
    expect(mocks.queryVariables).toEqual({ subject: "auth/user:usr_alice" });
    expect(mocks.queryOptions?.enabled).toBe(true);
    expect(mocks.listProps).toHaveLength(3);
    expect((mocks.listProps[0]?.rows as unknown[])).toHaveLength(1);
    expect((mocks.listProps[1]?.rows as unknown[])).toHaveLength(1);
    expect((mocks.listProps[2]?.rows as unknown[])).toHaveLength(1);
  });

  test("links model-backed grant targets through the shared resource router", () => {
    render(<PrincipalAccessTab />);
    const [target] = mocks.listProps[1]?.columns as Array<{
      render: (row: Record<string, unknown>) => ReactNode;
    }>;
    if (!target) throw new Error("expected the target column");
    const [grant] = mocks.listProps[1]?.rows as Record<string, unknown>[];
    const rendered = render(<>{target.render(grant!)}</>);
    expect(rendered.getByRole("link").getAttribute("href")).toBe("/projects/prj_1");
  });

  test("does not query when a record has no authorization principal", () => {
    mocks.record = {};
    const rendered = render(<PrincipalAccessTab />);
    expect(mocks.queryOptions?.enabled).toBe(false);
    expect(rendered.getByText("This record has no authorization principal.")).toBeTruthy();
  });

  test("shows loading while the enclosing record is unresolved", () => {
    mocks.record = null;
    const rendered = render(<PrincipalAccessTab />);
    expect(mocks.queryOptions?.enabled).toBe(false);
    expect(rendered.getByText("Loading access…")).toBeTruthy();
  });
});
