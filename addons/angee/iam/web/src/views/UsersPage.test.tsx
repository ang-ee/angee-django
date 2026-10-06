import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  Column,
  pageChildren,
  pageElementProps,
  parsePageActions,
  parsePageColumns,
  parsePageFields,
  type FormProps,
  type ListProps,
} from "@angee/ui";

vi.mock("../i18n", () => ({ useIamT: () => (key: string) => key }));
vi.mock("../PrincipalAccess", () => ({ usePrincipalAccessRecordTab: () => ({ id: "access" }) }));

// The page is called as a plain function to read its declarations, so its container reads are stubbed per address.
const contributed = vi.hoisted(() => ({ byAddress: {} as Record<string, readonly { content: unknown }[]> }));
vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return { ...actual, useContainer: (address: string) => contributed.byAddress[address] ?? [] };
});
afterEach(() => { contributed.byAddress = {}; });

import { USER_LIST_COLUMNS } from "../users-list";
import { UsersPage } from "./UsersPage";

function declarations(): { list: ListProps; form: FormProps } {
  const page = UsersPage();
  const children = pageChildren((page.props as { children?: ReactNode }).children);
  const list = children.map((child) => pageElementProps<ListProps>(child, "list")).find(Boolean);
  const form = children.map((child) => pageElementProps<FormProps>(child, "form")).find(Boolean);
  if (!list || !form) throw new Error("UsersPage must declare one list and one form");
  return { list, form };
}

describe("UsersPage", () => {
  test("renders its columns from iam.users#columns, so contributed seat columns sit among IAM's", () => {
    contributed.byAddress = {
      "iam.users#columns": [
        ...Object.values(USER_LIST_COLUMNS).slice(0, 2),
        { content: <Column field="seat" /> },
        ...Object.values(USER_LIST_COLUMNS).slice(2),
      ],
    };
    const { list } = declarations();
    expect(parsePageColumns(list.children).map((column) => column.field)).toEqual([
      "username", "email", "seat", "is_staff", "is_active", "last_login",
    ]);
    expect(list.presetIds).toEqual(["iam.users.active", "iam.users.deactivated"]);
  });

  test("the form selects the revision and leaves account verbs to the record actions container", () => {
    const { form } = declarations();
    const fields = parsePageFields(form.children);
    expect(fields.map((field) => field.name)).toContain("revision");
    expect(fields.find((field) => field.name === "password")?.createOnly).toBe(true);
    expect(parsePageActions(form.children)).toEqual([]);
  });
});
