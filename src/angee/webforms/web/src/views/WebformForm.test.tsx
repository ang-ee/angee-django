// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { FormProps, RecordToolbarContext } from "@angee/ui";

import { webformForm } from "./WebformForm";

const capture = vi.hoisted(() => ({ props: null as FormProps | null }));

// The record's own owner is replaced by one that keeps what the form declared; the
// form's verbs, its target choices and the route to the responses list answer plainly.
vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  Form: (props: FormProps) => {
    capture.props = props;
    return null;
  },
  useEnumOptions: () => [],
  useRecordActionMutation: () => [async () => undefined],
  useResourceRoute: () => "/webforms/responses?filtered",
}));

/** What the form's heading shows beside the title for one record. */
function heading(record: RecordToolbarContext["record"]): HTMLElement {
  const context = { recordId: record ? String(record.id) : null, record } as RecordToolbarContext;
  return render(<>{capture.props?.headerExtras?.(context)}</>).container;
}

afterEach(cleanup);

describe("WebformForm", () => {
  test("reads how many responses wait and when the first is removed, beside what its caller reads", () => {
    render(<webformForm.Component resource="webforms.Form" returning={["name"]} />);
    expect(capture.props?.returning).toEqual(["name", "undelivered_count", "undelivered_expires_at"]);
  });

  test("the record's heading carries the warning with its link while responses wait", () => {
    render(<webformForm.Component resource="webforms.Form" />);
    const container = heading({ id: "wbf_7", undelivered_count: 2, undelivered_expires_at: null });
    expect(container.querySelector("[role=status]")?.textContent).toContain("2 undelivered");
    expect(container.querySelector("a")?.getAttribute("href")).toBe("/webforms/responses?filtered");
  });

  test("the heading carries nothing while none waits, and keeps what its caller placed there", () => {
    render(<webformForm.Component resource="webforms.Form" headerExtras={() => "From the caller"} />);
    expect(heading({ id: "wbf_7", undelivered_count: 0, undelivered_expires_at: null }).textContent).toBe("From the caller");
  });
});
