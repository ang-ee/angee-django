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
  type RegisteredFormProps,
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

import { tagForm } from "./TagForm";
import { TagsPage } from "./TagsPage";

function pageDeclarationChildren(): { listChildren: ReactNode; formChildren: ReactNode } {
  const page = TagsPage();
  const pageProps = page.props as { children?: ReactNode; form?: unknown };
  // The page shows the registered tag form, the same one a picker's "Create “…”" opens.
  expect(pageProps.form).toBe(tagForm);
  const list = pageChildren(pageProps.children)
    .map((child) => pageElementProps<ListProps>(child, "list"))
    .find((props): props is ListProps => Boolean(props));
  const form = (tagForm.Component as (props: RegisteredFormProps) => { props: FormProps })(
    { resource: "tags.Tag" } as RegisteredFormProps,
  );
  if (!list) throw new Error("TagsPage must declare one list");
  return { listChildren: list.children, formChildren: form.props.children };
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
