// @vitest-environment happy-dom
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, test } from "vitest";

import {
  AppRuntimeProvider,
  pageChildren,
  pageElementProps,
  parsePageColumns,
  parsePageFields,
  type FormProps,
  type ListProps,
  type RegisteredFormProps,
} from "@angee/ui";

import { FOLDER_MODEL, folderForm } from "./FolderForm";
import { FoldersPage } from "./FoldersPage";
import { enPartiesMessages } from "./i18n";

interface FoldersPageProps {
  children?: ReactNode;
  resource?: string;
  form?: unknown;
  routed?: boolean;
  hideCreate?: boolean;
}

/** Render the page and its registered form under the runtime, and keep the declarations they rendered. */
function declarations(): { page: FoldersPageProps; list: ListProps; form: FormProps } {
  let page: { props: FoldersPageProps } | undefined;
  let form: { props: FormProps } | undefined;
  function Declarations(): null {
    page = FoldersPage() as typeof page;
    form = (folderForm.Component as (props: RegisteredFormProps) => { props: FormProps })({ resource: FOLDER_MODEL } as RegisteredFormProps);
    return null;
  }
  render(<AppRuntimeProvider runtime={{}}><Declarations /></AppRuntimeProvider>);
  const list = pageChildren(page?.props.children)
    .map((child) => pageElementProps<ListProps>(child, "list"))
    .find((props): props is ListProps => Boolean(props));
  if (!page || !list || !form) throw new Error("FoldersPage must declare one list and its form");
  return { page: page.props, list, form: form.props };
}

describe("FoldersPage", () => {
  test("browses the synced folders with the registered folder form and no create", () => {
    const { page } = declarations();

    // A directory's sync creates folders; the record opens the one registered form.
    expect(page).toMatchObject({ resource: FOLDER_MODEL, form: folderForm, routed: true, hideCreate: true });
  });

  test("lists each folder's name, directory and last change, and says where folders come from", () => {
    const { list } = declarations();

    expect(list.resource).toBe(FOLDER_MODEL);
    expect(parsePageColumns(list.children).map((column) => column.field)).toEqual(["name", "directory", "updated_at"]);
    expect(list.emptyContent).toBe(enPartiesMessages["folder.empty"]);
  });

  test("the record reads the folder's name as its title and its directory", () => {
    const { form } = declarations();

    expect(form.resource).toBe(FOLDER_MODEL);
    const fields = parsePageFields(form.children);
    expect(fields.map((field) => field.name)).toEqual(["name", "directory"]);
    expect(fields[0]?.title).toBe(true);
  });
});
