// @vitest-environment happy-dom
import { isValidElement, type PropsWithChildren } from "react";
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { pageChildren, pageElementProps, parsePageColumns, parsePageFields, type FormProps, type ListProps } from "@angee/ui";
import { StorageSettingsPage } from "./StorageSettingsPage";

afterEach(cleanup);

test("storage settings retain archive columns and leave archive edits to shared record actions", () => {
  const { result } = renderHook(StorageSettingsPage);
  if (!isValidElement<PropsWithChildren>(result.current)) throw new Error("StorageSettingsPage must declare settings sections");
  const forms: FormProps[] = [];
  for (const section of pageChildren(result.current.props.children)) {
    if (!isValidElement<PropsWithChildren>(section)) throw new Error("Expected a settings section");
    for (const resource of pageChildren(section.props.children)) {
      if (!isValidElement<PropsWithChildren>(resource)) throw new Error("Expected a drawer resource list");
      const children = pageChildren(resource.props.children);
      const list = children.map((child) => pageElementProps<ListProps>(child, "list")).find(Boolean);
      const form = children.map((child) => pageElementProps<FormProps>(child, "form")).find(Boolean);
      if (!list || !form) throw new Error("Each storage section must declare one list and one form");
      expect(parsePageColumns(list.children).map((column) => column.field)).toContain("is_archived");
      forms.push(form);
    }
  }
  expect(forms.map((form) => [form.resource, parsePageFields(form.children).map((field) => field.name)])).toEqual([
    ["storage.Drive", ["name", "slug", "prefix", "description"]],
    ["storage.Backend", ["label", "slug", "backend_class", "backend_config"]],
  ]);
});
