// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import {
  formatDate, pageChildren, pageElementProps, parsePageColumns,
  type ListProps, type ResourceListProps,
} from "@angee/ui";

import { FormsPage } from "./FormsPage";

const capture = vi.hoisted(() => ({ props: null as ResourceListProps | null }));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  ResourceList: (props: ResourceListProps) => {
    capture.props = props;
    return null;
  },
}));

const EXPIRY = "2031-03-09T12:00:00+00:00";

/** The list the page declared. */
function list(): ListProps | undefined {
  return pageChildren(capture.props?.children)
    .map((child) => pageElementProps<ListProps>(child, "list"))
    .find((candidate): candidate is ListProps => Boolean(candidate));
}

afterEach(cleanup);

describe("FormsPage", () => {
  test("reads each form's undelivered count and when the first of them is removed", () => {
    render(<FormsPage />);
    expect(list()?.fields).toEqual(["undelivered_expires_at"]);
    expect(parsePageColumns(list()?.children).map((column) => column.field))
      .toEqual(["name", "status", "target_class", "undelivered_count", "updated_at"]);
  });

  test("a form says how many responses wait and when the first is removed, and nothing while none waits", () => {
    render(<FormsPage />);
    const column = parsePageColumns(list()?.children).find((candidate) => candidate.field === "undelivered_count");
    expect(column?.header).toBe("Undelivered");
    const cell = (row: Record<string, unknown>) => render(<>{column?.render?.(row)}</>).container.textContent;
    expect(cell({ undelivered_count: 3, undelivered_expires_at: EXPIRY }))
      .toBe(`3 undelivered, the first expires on ${formatDate(EXPIRY)}`);
    expect(cell({ undelivered_count: 2, undelivered_expires_at: null })).toBe("2 undelivered");
    expect(cell({ undelivered_count: 0, undelivered_expires_at: null })).toBe("");
  });
});
