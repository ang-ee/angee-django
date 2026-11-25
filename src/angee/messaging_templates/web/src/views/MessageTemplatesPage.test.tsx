// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import {
  pageChildren,
  pageElementProps,
  parsePageActions,
  parsePageColumns,
  parsePageFields,
  parsePageGroups,
  type FormProps,
  type ListProps,
  type RecordTabDescriptor,
  type ResourceListProps,
} from "@angee/ui";

const capture = vi.hoisted(() => ({ props: null as FormProps | null }));

// The record's own owner is replaced by one that keeps what the form declared; the verbs and the
// enum options answer plainly.
vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  Form: (props: FormProps) => {
    capture.props = props;
    return null;
  },
  useEnumOptions: () => [],
  useRecordActionMutation: () => [async () => undefined],
}));

import { enMessagingTemplatesMessages as M } from "../i18n";
import { MESSAGE_TEMPLATE_PRESET_IDS } from "../resources";
import { messageTemplateForm } from "./MessageTemplateForm";
import { MessageTemplateNotice } from "./MessageTemplateNotice";
import { MessageTemplatesPage } from "./MessageTemplatesPage";

afterEach(cleanup);

/** The page's own declaration, kept for the shared page parsers. */
function pageDeclaration(): { page: ResourceListProps; list: ListProps } {
  let element: ReactElement<ResourceListProps> | undefined;
  function Declarations(): null {
    element = MessageTemplatesPage() as ReactElement<ResourceListProps>;
    return null;
  }
  render(<Declarations />);
  const list = pageChildren(element?.props.children)
    .map((child) => pageElementProps<ListProps>(child, "list"))
    .find((props): props is ListProps => Boolean(props));
  if (!element || !list) throw new Error("MessageTemplatesPage must declare one list.");
  return { page: element.props, list };
}

/** The registered form's declarations, as a resource surface renders it. */
function formDeclaration(props: Partial<FormProps> = {}): FormProps {
  capture.props = null;
  render(<messageTemplateForm.Component resource="messaging_templates.MessageTemplate" {...props} />);
  if (!capture.props) throw new Error("MessageTemplateForm must render one form.");
  return capture.props;
}

const children = (form: FormProps): ReactNode => form.children;

describe("MessageTemplatesPage", () => {
  test("lists every row in one list grouped by kind, with the library's presets, and opens each in the registered form", () => {
    const { page, list } = pageDeclaration();

    expect(page.form).toBe(messageTemplateForm);
    expect(page.routed).toBe(true);
    expect(list.defaultGroups).toEqual({ list: { field: "kind" } });
    expect(list.presetIds).toBe(MESSAGE_TEMPLATE_PRESET_IDS);
    expect(parsePageColumns(list.children).map((column) => [column.field, column.header])).toEqual([
      ["name", M["col.name"]], ["language", M["col.language"]], ["is_baseline", M["col.shipped"]],
      ["is_effective", M["col.effective"]], ["has_updated_default", M["col.defaultUpdated"]],
    ]);
  });
});

describe("MessageTemplateForm", () => {
  test("asks for the identity, then a template's message, the content, its values and its details", () => {
    const form = formDeclaration();

    expect(parsePageFields(children(form)).map((field) => field.name)).toEqual([
      "name",
      "kind", "key", "language", "carries_message_fields",
      "subject", "preheader", "layout",
      "format", "source", "text_source",
      "variables",
      "description",
      "sample",
    ]);
    expect(parsePageGroups(children(form)).map((group) => [group.label, group.pane ?? null])).toEqual([
      [M["form.identity"], null], [M["form.message"], null], [M["form.content"], null], [M["form.variables"], null],
      [M["form.details"], null], [M["form.sample"], "sample"],
    ]);
    expect(parsePageFields(children(form)).find((field) => field.name === "name")?.title).toBe(true);
  });

  test("takes the kind, key and language on create only, and reads every enum through its lowercase options", () => {
    const fields = parsePageFields(children(formDeclaration()));

    expect(fields.filter((field) => field.createOnly).map((field) => field.name)).toEqual(["kind", "key", "language"]);
    expect(fields.filter((field) => field.widget === "select").map((field) => field.name)).toEqual(["kind", "format"]);
    expect(fields.filter((field) => field.options !== undefined).map((field) => field.name)).toEqual(["kind", "format"]);
  });

  test("shows the subject, preheader and layout only for a kind the server says carries them", () => {
    const fields = parsePageFields(children(formDeclaration()));
    const conditional = fields.filter((field) => field.showWhen);

    expect(conditional.map((field) => field.name)).toEqual(["subject", "preheader", "layout"]);
    for (const field of conditional) {
      expect(field.showWhen?.({ carries_message_fields: true })).toBe(true);
      expect(field.showWhen?.({ carries_message_fields: false })).toBe(false);
      expect(field.showWhen?.({})).toBe(false);
    }
    expect(fields.find((field) => field.name === "carries_message_fields")).toMatchObject({ hidden: true, readOnly: true });
  });

  test("lifts no field above the sheet as the record's body", () => {
    const fields = parsePageFields(children(formDeclaration()));

    expect(fields.filter((field) => field.body !== undefined).map((field) => [field.name, field.body])).toEqual([
      ["source", false], ["text_source", false], ["description", false],
    ]);
    expect(fields.filter((field) => field.widget === "textarea").every((field) => field.body === false)).toBe(true);
  });

  test("leaves the values and the sample off the create form, where the server seeds them", () => {
    const groups = parsePageGroups(children(formDeclaration()));

    expect(groups.filter((group) => group.savedOnly).map((group) => group.label)).toEqual([
      M["form.variables"], M["form.sample"],
    ]);
  });

  test("offers Override to whoever may preview a shipped default nobody overrides, and Keep override to a writer", () => {
    const actions = parsePageActions(children(formDeclaration()));
    const override = actions.find((action) => action.id === "override");
    const keep = actions.find((action) => action.id === "keep-override");

    expect(actions.map((action) => [action.id, action.label, action.permission])).toEqual([
      ["override", M["action.override"], "preview"], ["keep-override", M["action.keepOverride"], "write"],
    ]);
    expect(override?.visibleWhen?.({ is_baseline: true, is_overridden: false })).toBe(true);
    expect(override?.visibleWhen?.({ is_baseline: true, is_overridden: true })).toBe(false);
    expect(override?.visibleWhen?.({ is_baseline: false, is_overridden: false })).toBe(false);
    expect(keep?.visibleWhen?.({ is_baseline: false, has_updated_default: true })).toBe(true);
    expect(keep?.visibleWhen?.({ is_baseline: false, has_updated_default: false })).toBe(false);
  });

  test("shows Delete where the row projects delete", () => {
    const form = formDeclaration();

    expect(form.deleteVisibleWhen?.({ permissions: ["delete", "preview", "write"] })).toBe(true);
    expect(form.deleteVisibleWhen?.({ permissions: ["preview"] })).toBe(false);
  });

  test("reads the row facts it shows beside its caller's selection, and puts its Preview pane before its caller's panes", () => {
    const extra: RecordTabDescriptor = { id: "extra", label: "Extra", render: () => null };
    const form = formDeclaration({ returning: ["display_name"], recordTabs: [extra] });

    expect(form.returning).toEqual(["display_name", "is_baseline", "is_overridden", "has_updated_default"]);
    expect(form.recordTabs?.map((tab) => tab.id)).toEqual(["preview", "extra"]);
    const preview = form.recordTabs?.[0];
    expect(preview?.presentation).toBe("full-bleed");
    expect(preview?.visibleWhen?.({ permissions: ["preview"] }, null)).toBe(true);
    expect(preview?.visibleWhen?.({ permissions: [] }, null)).toBe(false);
  });
});

describe("MessageTemplateNotice", () => {
  type Sentence = "notice.shipped" | "notice.shippedOverride" | "notice.overridden" | "notice.defaultUpdated"
    | "notice.keepHint" | "notice.deleteNew" | "notice.live" | "notice.deleteDefault";
  const SENTENCES: readonly Sentence[] = [
    "notice.shipped", "notice.shippedOverride", "notice.overridden", "notice.defaultUpdated", "notice.keepHint",
    "notice.deleteNew", "notice.live", "notice.deleteDefault",
  ];
  const shipped = ["preview"];
  const writable = ["delete", "preview", "write"];

  /** The notice for one row: its alert role, or null when there is none, and its text. */
  function notice(record: Record<string, unknown>): { role: string | null; text: string } {
    const { container } = render(<MessageTemplateNotice record={{ id: "mtl_row", ...record }} />);
    const alert = container.querySelector("[role=status], [role=alert]");
    return { role: alert?.getAttribute("role") ?? null, text: container.textContent ?? "" };
  }

  test.each<[string, Record<string, unknown>, readonly Sentence[]]>([
    ["a shipped default nobody overrides, to an administrator",
      { is_baseline: true, is_overridden: false, permissions: shipped }, ["notice.shipped", "notice.shippedOverride"]],
    ["an overridden shipped default, to an administrator",
      { is_baseline: true, is_overridden: true, permissions: shipped }, ["notice.overridden"]],
    ["an override whose default changed, to an administrator",
      { is_baseline: false, has_updated_default: true, permissions: writable },
      ["notice.defaultUpdated", "notice.keepHint", "notice.deleteNew"]],
    ["the deployment's own row or a current override, to an administrator",
      { is_baseline: false, has_updated_default: false, permissions: writable }, ["notice.live", "notice.deleteDefault"]],
    ["a shipped default nobody overrides, to a member",
      { is_baseline: true, is_overridden: false, permissions: [] }, ["notice.shipped"]],
    ["an override whose default changed, to a member",
      { is_baseline: false, has_updated_default: true, permissions: [] }, ["notice.defaultUpdated"]],
  ])("says what %s is, and each hint only where its verb is the reader's", (_case, record, shown) => {
    const { role, text } = notice(record);

    expect(role).toBe("status");
    // "notice.overridden" restates "notice.shipped" before its own sentence; the other sentences are each their own.
    const contained = SENTENCES.filter((key) => text.includes(M[key]!));
    expect(contained.filter((key) => key !== "notice.shipped" || !shown.includes("notice.overridden")))
      .toEqual(SENTENCES.filter((key) => shown.includes(key)));
  });

  test("says nothing to a member about a row whose only sentences are a writer's", () => {
    expect(notice({ is_baseline: false, has_updated_default: false, permissions: [] })).toEqual({ role: null, text: "" });
  });
});
