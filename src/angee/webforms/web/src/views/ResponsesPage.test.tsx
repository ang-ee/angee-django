// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import {
  pageChildren, pageElementProps, parsePageActions, parsePageColumns, parsePageFields,
  type FormProps, type ListProps, type ResourceListProps,
} from "@angee/ui";

import { ResponsesPage } from "./ResponsesPage";

const capture = vi.hoisted(() => ({
  props: null as ResourceListProps | null,
  invalidated: {} as Record<string, readonly string[] | undefined>,
}));

// The page's verbs are the server's own; here each records which lists it refreshes.
vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  ResourceList: (props: ResourceListProps) => {
    capture.props = props;
    return null;
  },
  useRecordActionMutation: (field: string, options: { invalidateModels?: readonly string[] }) => {
    capture.invalidated[field] = options.invalidateModels;
    return [async () => undefined];
  },
}));

/** The list or form the page declared. */
function declared<TProps>(kind: "list" | "form"): TProps | undefined {
  return pageChildren(capture.props?.children)
    .map((child) => pageElementProps<TProps>(child, kind))
    .find((candidate): candidate is TProps => Boolean(candidate));
}

afterEach(cleanup);

describe("ResponsesPage", () => {
  test("offers one filter choice for the responses still waiting: pending and failed together", () => {
    render(<ResponsesPage />);
    expect(capture.props?.filterOptions).toEqual([
      { id: "undelivered", label: "Undelivered", filter: { delivery: { inList: ["pending", "failed"] } } },
    ]);
  });

  test("lists what became of each response and the day its form's retention removes it", () => {
    render(<ResponsesPage />);
    const columns = parsePageColumns(declared<ListProps>("list")?.children);
    expect(columns.map((column) => column.field))
      .toEqual(["form", "version", "language", "contact", "delivery", "delivery_error", "created_at", "expires_at"]);
    expect(columns.at(-1)?.header).toBe("Expires");
  });

  test("a person's responses are found by their contact, across forms, beside the choice for those still waiting", () => {
    render(<ResponsesPage />);
    expect(capture.props?.search).toEqual({
      shortcuts: [
        { kind: "text", field: "contact", label: "Contact (trimmed, lower case)" },
        { kind: "toggle", id: "undelivered" },
      ],
    });
    // Erasing is offered on each record found, until it is done.
    const erase = parsePageActions(declared<FormProps>("form")?.children).find((action) => action.id === "erase");
    expect(erase?.visibleWhen?.({ contact: "ada@example.org", erased_at: null })).toBe(true);
    expect(erase?.visibleWhen?.({ contact: "", erased_at: "2031-03-09T12:00:00+00:00" })).toBe(false);
    const contact = parsePageFields(declared<FormProps>("form")?.children).find((candidate) => candidate.name === "contact");
    expect(contact?.showWhen?.({ contact: "ada@example.org" })).toBe(true);
    expect(contact?.showWhen?.({ contact: "" })).toBe(false);
  });

  test("the record shows the expiry only when the response has one", () => {
    render(<ResponsesPage />);
    const field = parsePageFields(declared<FormProps>("form")?.children).find((candidate) => candidate.name === "expires_at");
    expect(field?.label).toBe("Expires");
    expect(field?.showWhen?.({ expires_at: "2031-03-09T12:00:00+00:00" })).toBe(true);
    expect(field?.showWhen?.({ expires_at: null })).toBe(false);
  });

  test("delivering and dismissing are offered only to a response still waiting, and refresh its form's count", () => {
    render(<ResponsesPage />);
    const actions = parsePageActions(declared<FormProps>("form")?.children);
    const offered = (delivery: string) => actions.filter((action) => action.visibleWhen?.({ delivery }) !== false && action.id !== "erase")
      .map((action) => action.id);
    expect(offered("FAILED")).toEqual(["deliver", "dismiss"]);
    expect(offered("PENDING")).toEqual(["deliver", "dismiss"]);
    expect(offered("DELIVERED")).toEqual([]);
    expect(capture.invalidated.deliver_webform_response).toEqual(["webforms.Response", "webforms.Form"]);
    expect(capture.invalidated.dismiss_webform_response).toEqual(["webforms.Response", "webforms.Form"]);
  });
});
