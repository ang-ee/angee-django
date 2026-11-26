// @vitest-environment happy-dom
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, test } from "vitest";

import {
  AppRuntimeProvider,
  containersFromChildren,
  pageChildren,
  pageElementProps,
  parsePageColumns,
  parsePageFields,
  parsePageGroups,
  type FormProps,
  type ListProps,
  type RegisteredFormProps,
} from "@angee/ui";

import { BRAND_KIT_MODEL, brandKitForm } from "./BrandKitForm";
import { BrandKitsPage } from "./BrandKitsPage";

/**
 * Render the page and its registered form under the runtime and keep the
 * declarations they rendered for the shared page parsers: a misspelled field
 * would otherwise vanish from the form without an error.
 */
function pageDeclarationChildren(): { listChildren: ReactNode; formChildren: ReactNode } {
  let page: { props: { children?: ReactNode; form?: unknown } } | undefined;
  let form: { props: FormProps } | undefined;
  function Declarations(): null {
    page = BrandKitsPage() as typeof page;
    form = (brandKitForm.Component as (props: RegisteredFormProps) => { props: FormProps })(
      { resource: BRAND_KIT_MODEL } as RegisteredFormProps,
    );
    return null;
  }
  render(<AppRuntimeProvider runtime={{ containers: containersFromChildren([], {}) }}><Declarations /></AppRuntimeProvider>);
  expect(page?.props.form).toBe(brandKitForm);
  const list = pageChildren(page?.props.children)
    .map((child) => pageElementProps<ListProps>(child, "list"))
    .find((props): props is ListProps => Boolean(props));
  if (!list || !form) throw new Error("BrandKitsPage must declare one list and its form");
  return { listChildren: list.children, formChildren: form.props.children };
}

describe("BrandKitsPage", () => {
  test("lists kits by name, default flag and brand colour", () => {
    const { listChildren } = pageDeclarationChildren();

    expect(parsePageColumns(listChildren).map((column) => column.field)).toEqual(
      ["name", "is_default", "brand", "updated_at"],
    );
  });

  test("edits every writable kit field, never a logo, and shows contrast only on a saved kit", () => {
    const { formChildren } = pageDeclarationChildren();

    expect(parsePageFields(formChildren).map((field) => field.name)).toEqual([
      "name", "is_default",
      "brand", "accent", "neutral", "canvas", "surface", "rail", "success", "warning", "danger", "info",
      "contrast_warnings", "font_heading", "font_body", "footer_organization", "footer_address", "voice",
    ]);
    const contrast = parsePageGroups(formChildren).find((group) =>
      group.fields.some((field) => field.name === "contrast_warnings"));
    expect(contrast?.savedOnly).toBe(true);
  });
});
