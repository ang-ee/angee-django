import type { ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";

import {
  pageChildren,
  pageElementProps,
  parsePageColumns,
  parsePageFacets,
  parsePageFields,
  type FormProps,
  type ListProps,
} from "@angee/ui";

vi.mock("../i18n", () => ({
  useTagsT: () => (key: string) => key,
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useSlot: () => [],
  };
});

import { TagsPage } from "./TagsPage";

function pageDeclarationChildren(): { listChildren: ReactNode; formChildren: ReactNode } {
  const page = TagsPage();
  const children = (page.props as { children?: ReactNode }).children;
  const declarations = pageChildren(children);
  const list = declarations
    .map((child) => pageElementProps<ListProps>(child, "list"))
    .find((props): props is ListProps => Boolean(props));
  const form = declarations
    .map((child) => pageElementProps<FormProps>(child, "form"))
    .find((props): props is FormProps => Boolean(props));
  if (!list || !form) throw new Error("TagsPage must declare one list and one form");
  return { listChildren: list.children, formChildren: form.children };
}

describe("TagsPage", () => {
  test("declares the shared base vocabulary shape", () => {
    const { listChildren, formChildren } = pageDeclarationChildren();

    expect(parsePageFacets(listChildren)).toEqual([]);
    expect(parsePageColumns(listChildren).map((column) => column.field)).toEqual([
      "name",
      "color",
      "updated_at",
    ]);
    expect(parsePageFields(formChildren).map((field) => field.name)).toEqual([
      "name",
      "color",
      "is_archived",
    ]);
  });
});
