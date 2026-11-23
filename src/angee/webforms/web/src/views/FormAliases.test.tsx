// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import {
  pageChildren, pageElementProps, parsePageColumns, parsePageFields,
  type DrawerResourceListProps, type FormProps, type ListProps, type RecordPanelContext,
} from "@angee/ui";

import { publicAddress } from "../address";
import { FormAliases } from "./FormAliases";

const capture = vi.hoisted(() => ({ props: null as DrawerResourceListProps | null }));

const formSurface = (formReadOnly: boolean) => ({ formReadOnly }) as RecordPanelContext["form"];

vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  DrawerResourceList: (props: DrawerResourceListProps) => {
    capture.props = props;
    return null;
  },
}));

/** The list or form the tab declared for its drawer list. */
function declared<TProps>(kind: "list" | "form"): TProps | undefined {
  return pageChildren(capture.props?.children)
    .map((child) => pageElementProps<TProps>(child, kind))
    .find((candidate): candidate is TProps => Boolean(candidate));
}

afterEach(cleanup);

describe("FormAliases", () => {
  test("lists one form's addresses and adds an address to that form, with the slug's rule as help", () => {
    render(<FormAliases recordId="wbf_7" form={formSurface(false)} />);
    expect(capture.props).toMatchObject({
      resource: "webforms.FormAlias",
      presentation: "embedded",
      baseFilter: { form: { exact: "wbf_7" } },
      createDefaults: { form: "wbf_7" },
      hideCreate: false,
    });
    expect(parsePageFields(declared<FormProps>("form")?.children)).toMatchObject([
      { name: "form", readOnly: true },
      { name: "slug", title: true, description: "Lower-case letters and numbers, with single hyphens between." },
      { name: "language" },
    ]);
  });

  test("links each address to the public page, opened as a document beside the app", () => {
    render(<FormAliases recordId="wbf_7" form={formSurface(false)} />);
    const columns = parsePageColumns(declared<ListProps>("list")?.children);
    expect(columns.map((column) => column.field)).toEqual(["slug", "language", "created_at"]);
    const { container } = render(<>{columns[0]?.render?.({ slug: "semilla" })}</>);
    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe(publicAddress("semilla"));
    expect(link?.textContent).toBe(publicAddress("semilla"));
    expect(link?.getAttribute("target")).toBe("_blank");
    expect(link?.getAttribute("rel")?.split(" ")).toEqual(expect.arrayContaining(["external", "noopener"]));
  });

  test("inherits a read-only form: no address is added or edited", () => {
    render(<FormAliases recordId="wbf_7" form={formSurface(true)} />);
    expect(capture.props?.hideCreate).toBe(true);
    expect(declared<FormProps>("form")?.readOnly).toBe(true);
  });
});
