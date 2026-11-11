// @vitest-environment happy-dom
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, test } from "vitest";

import {
  AppRuntimeProvider,
  Column,
  containersFromChildren,
  pageChildren,
  pageElementProps,
  parsePageActions,
  parsePageColumns,
  parsePageFields,
  type FormProps,
  type ListProps,
} from "@angee/ui";

import { USER_LIST_COLUMNS } from "../users-list";
import { UsersPage } from "./UsersPage";

/** Render the page under the runtime, with IAM's real columns and a seat column another addon places by sequence. */
function declarations(): { page: { baseFilter?: unknown }; list: ListProps; form: FormProps } {
  let page: { props: { children?: ReactNode; baseFilter?: unknown } } | undefined;
  function Declarations(): null {
    page = UsersPage() as typeof page;
    return null;
  }
  const [first, second, ...rest] = Object.entries(USER_LIST_COLUMNS);
  // IAM's manifest declares the columns container; a runtime resolves only declared addresses.
  const containers = containersFromChildren([{ address: "iam.users#columns" }], {
    "iam.users#columns": Object.fromEntries([first!, second!, ["seats.seat", { sequence: 25, content: <Column field="seat" /> }], ...rest]),
  });
  render(<AppRuntimeProvider runtime={{ containers, auth: { user: { id: "usr_admin", name: "Admin" }, status: "authenticated", hasRole: () => false } }}>
    <Declarations />
  </AppRuntimeProvider>);
  const children = pageChildren(page?.props.children);
  const list = children.map((child) => pageElementProps<ListProps>(child, "list")).find(Boolean);
  const form = children.map((child) => pageElementProps<FormProps>(child, "form")).find(Boolean);
  if (!page || !list || !form) throw new Error("UsersPage must declare one list and one form");
  return { page: page.props, list, form };
}

describe("UsersPage", () => {
  test("renders its columns from iam.users#columns, so contributed seat columns sit among IAM's", () => {
    const { list } = declarations();
    expect(parsePageColumns(list.children).map((column) => column.field)).toEqual([
      "username", "email", "seat", "is_staff", "is_active", "last_login",
    ]);
    expect(list.presetIds).toEqual(["iam.users.active", "iam.users.deactivated"]);
  });

  test("lists the viewer's people directory, which is empty for anyone who manages no one", () => {
    expect(declarations().page.baseFilter).toEqual({ directory: { exact: true } });
  });

  test("the form selects the revision and leaves account verbs to the record actions container", () => {
    const { form } = declarations();
    const fields = parsePageFields(form.children);
    expect(fields.map((field) => field.name)).toContain("revision");
    expect(fields.find((field) => field.name === "password")?.createOnly).toBe(true);
    expect(parsePageActions(form.children)).toEqual([]);
  });
});
