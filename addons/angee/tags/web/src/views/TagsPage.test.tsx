import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  Column,
  Field,
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

// The page is called as a plain function to read its declarations, so its container reads are stubbed per address.
const contributed = vi.hoisted(() => ({ byAddress: {} as Record<string, readonly { content: unknown }[]> }));
vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useContainer: (address: string) => contributed.byAddress[address] ?? [],
  };
});
afterEach(() => { contributed.byAddress = {}; });

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

  test("places scope contributions from tags.tags#columns and #fields beside the base declarations", () => {
    contributed.byAddress = {
      "tags.tags#columns": [{ content: <Column field="scope" /> }],
      "tags.tags#fields": [{ content: <Field name="scope" /> }],
    };
    const { listChildren, formChildren } = pageDeclarationChildren();

    expect(parsePageColumns(listChildren).map((column) => column.field)).toEqual(["name", "color", "scope", "updated_at"]);
    expect(parsePageFields(formChildren).map((field) => field.name)).toEqual(["name", "color", "scope", "is_archived"]);
  });
});
