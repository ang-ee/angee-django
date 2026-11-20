// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { parsePageFields, type FormProps, type RecordToolbarContext } from "@angee/ui";

import { webformForm } from "./WebformForm";

const capture = vi.hoisted(() => ({ props: null as FormProps | null }));

// The record's own owner is replaced by one that keeps what the form declared; the
// form's verbs, its target choices and the route to the responses list answer plainly.
// Of the targets, `enquiry` declares a configuration this form has fields for, `opaque` one it has
// none for, and `store` declares none; one theme is installed.
vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  Form: (props: FormProps) => {
    capture.props = props;
    return null;
  },
  useEnumOptions: () => [],
  useRecordActionMutation: () => [async () => undefined],
  useResourceRoute: () => "/webforms/responses?filtered",
  useImplConfigFields: (_resource: string, field: string, _supplied: unknown, configField: string) => ({
    fields: [{
      name: `${configField}.queue`, label: "Queue",
      showWhen: (values: Record<string, unknown>) => values[field] === "enquiry",
    }],
    hasSchema: (value: unknown) => value === "enquiry" || value === "opaque",
  }),
  useAppRuntime: () => ({ themes: [{ definition: { id: "house.paper", labelKey: "house.paper.label" } }] }),
  useT: () => (key: string) => `themes:${key}`,
}));

const fields = () => parsePageFields(capture.props?.children);

/** What the form's heading shows beside the title for one record. */
function heading(record: RecordToolbarContext["record"]): HTMLElement {
  const context = { recordId: record ? String(record.id) : null, record } as RecordToolbarContext;
  return render(<>{capture.props?.headerExtras?.(context)}</>).container;
}

afterEach(cleanup);

describe("WebformForm", () => {
  test("asks in the order a person decides: name, target and its configuration, then theme and retention", () => {
    render(<webformForm.Component resource="webforms.Form" />);
    expect(fields().map((field) => field.name)).toEqual([
      "name", "status", "target_class", "target_config", "target_config.queue", "theme_id", "retention_days",
    ]);
  });

  test("a target's configuration is edited by its own fields, shown raw only when the form has none for it, and not shown when the target takes none", () => {
    render(<webformForm.Component resource="webforms.Form" />);
    const shown = (target: string) => fields().filter((field) => field.name.startsWith("target_config"))
      .filter((field) => field.showWhen?.({ target_class: target }) !== false).map((field) => field.name);
    expect(shown("enquiry")).toEqual(["target_config.queue"]);
    expect(shown("opaque")).toEqual(["target_config"]);
    expect(shown("store")).toEqual([]);
  });

  test("choosing another target starts its configuration empty, and choosing the stored one again gives its stored configuration back", () => {
    render(<webformForm.Component resource="webforms.Form" />);
    const target = fields().find((field) => field.name === "target_class");
    // As the server reads the stored target back, in its own casing.
    const loaded = { target_class: "ENQUIRY", target_config: { queue: "front desk" } };
    expect(target?.prefill?.("store", { target_config: { queue: "front desk" } }, loaded)).toEqual({ target_config: {} });
    expect(target?.prefill?.("opaque", { target_config: {} }, loaded)).toEqual({ target_config: {} });
    expect(target?.prefill?.("enquiry", { target_config: {} }, loaded)).toEqual({ target_config: { queue: "front desk" } });
    // A new form has no stored target: every choice starts empty.
    expect(target?.prefill?.("store", {}, { target_class: "store" })).toEqual({ target_config: {} });
    expect(target?.prefill?.("enquiry", {})).toEqual({ target_config: {} });
    // Nothing is kept back: without `prefillPreserveDirty` a preset replaces every field it names.
    expect(target?.prefillPreserveDirty).toBeUndefined();
  });

  test("the theme is a choice among the installed themes, or the deployment's default", () => {
    render(<webformForm.Component resource="webforms.Form" />);
    const theme = fields().find((field) => field.name === "theme_id");
    expect(theme?.widget).toBe("select");
    expect(theme?.options).toEqual([
      { value: "", label: "The deployment's default" },
      { value: "house.paper", label: "themes:house.paper.label" },
    ]);
  });

  test("keeps its Addresses tab beside the tabs its caller adds", () => {
    render(<webformForm.Component resource="webforms.Form" recordTabs={[{ id: "extra", label: "Extra", render: () => null }]} />);
    expect(capture.props?.recordTabs?.map((tab) => tab.id)).toEqual(["addresses", "extra"]);
    render(<webformForm.Component resource="webforms.Form" />);
    expect(capture.props?.recordTabs?.map((tab) => tab.id)).toEqual(["addresses"]);
  });

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
