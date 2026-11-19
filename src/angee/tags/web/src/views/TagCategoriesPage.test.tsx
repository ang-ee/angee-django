// @vitest-environment happy-dom
import { isValidElement, type PropsWithChildren } from "react";
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { pageChildren, pageElementProps, parsePageColumns, parsePageFields, type FormProps, type ListProps } from "@angee/ui";
import { TagCategoriesPage } from "./TagCategoriesPage";

afterEach(cleanup);

test("category declarations leave archive filtering and record actions to the shared resource owners", () => {
  const { result } = renderHook(TagCategoriesPage);
  if (!isValidElement<PropsWithChildren>(result.current)) throw new Error("TagCategoriesPage must declare a resource page");
  const children = pageChildren(result.current.props.children);
  const list = children.map((child) => pageElementProps<ListProps>(child, "list")).find(Boolean);
  const form = children.map((child) => pageElementProps<FormProps>(child, "form")).find(Boolean);
  if (!list || !form) throw new Error("TagCategoriesPage must declare one list and one form");

  expect(list.defaultGroup).toBeUndefined();
  expect(list.defaultGroups).toBeUndefined();
  expect(parsePageColumns(list.children).map((column) => column.field)).toEqual(["name", "max_per_record", "position", "updated_at"]);
  expect(parsePageFields(form.children).map((field) => field.name)).toEqual(["name", "max_per_record", "position", "description"]);
});
