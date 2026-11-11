// @vitest-environment happy-dom
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, test } from "vitest";

import {
  AppRuntimeProvider,
  Column,
  Field,
  containersFromChildren,
  pageChildren,
  pageElementProps,
  parsePageColumns,
  parsePageFacets,
  parsePageFields,
  type ContainerChild,
  type FormProps,
  type ListProps,
  type RegisteredFormProps,
} from "@angee/ui";

import { tagForm } from "./TagForm";

// The tags manifest declares these containers; a runtime resolves only declared addresses.
const TAG_CONTAINERS = ["tags.tags#facets", "tags.tags#columns", "tags.tags#fields"].map((address) => ({ address: address as `${string}#${string}` }));
import { TagsPage } from "./TagsPage";

/**
 * Render the page and its registered form under the runtime, with real containers and translator,
 * and keep the declarations they rendered for the shared page parsers.
 */
function pageDeclarationChildren(
  children: Readonly<Record<string, Readonly<Record<string, ContainerChild>>>> = {},
): { listChildren: ReactNode; formChildren: ReactNode } {
  let page: { props: { children?: ReactNode; form?: unknown } } | undefined;
  let form: { props: FormProps } | undefined;
  function Declarations(): null {
    page = TagsPage() as typeof page;
    form = (tagForm.Component as (props: RegisteredFormProps) => { props: FormProps })({ resource: "tags.Tag" } as RegisteredFormProps);
    return null;
  }
  render(<AppRuntimeProvider runtime={{ containers: containersFromChildren(TAG_CONTAINERS, children) }}><Declarations /></AppRuntimeProvider>);
  // The page shows the registered tag form, the same one a picker's "Create “…”" opens.
  expect(page?.props.form).toBe(tagForm);
  const list = pageChildren(page?.props.children)
    .map((child) => pageElementProps<ListProps>(child, "list"))
    .find((props): props is ListProps => Boolean(props));
  if (!list || !form) throw new Error("TagsPage must declare one list and its form");
  return { listChildren: list.children, formChildren: form.props.children };
}

describe("TagsPage", () => {
  test("declares the shared base vocabulary shape", () => {
    const { listChildren, formChildren } = pageDeclarationChildren();

    expect(parsePageFacets(listChildren)).toEqual([]);
    expect(parsePageColumns(listChildren).map((column) => column.field)).toEqual(["name", "color", "attention", "updated_at"]);
    expect(parsePageFields(formChildren).map((field) => field.name)).toEqual(["name", "color", "attention", "is_archived"]);
  });

  test("places scope contributions from tags.tags#columns and #fields beside the base declarations", () => {
    const { listChildren, formChildren } = pageDeclarationChildren({
      "tags.tags#columns": { "test.scope": { content: <Column field="scope" /> } },
      "tags.tags#fields": { "test.scope": { content: <Field name="scope" /> } },
    });

    expect(parsePageColumns(listChildren).map((column) => column.field)).toEqual(["name", "color", "attention", "scope", "updated_at"]);
    expect(parsePageFields(formChildren).map((field) => field.name)).toEqual(["name", "color", "attention", "scope", "is_archived"]);
  });
});
