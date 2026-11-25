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

  test("shows the three flags with one widget, whichever the metadata gives each", () => {
    const { list } = pageDeclaration();
    const flags = parsePageColumns(list.children).filter((column) => column.field !== "name" && column.field !== "language");

    expect(flags.map((column) => column.field)).toEqual(["is_baseline", "is_effective", "has_updated_default"]);
    expect(new Set(flags.map((column) => column.widget)).size).toBe(1);
    expect(flags[0]?.widget).toBe("boolean");
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
    const conditional = fields.filter((field) => field.showWhen && field.name !== "text_source");

    expect(fields.filter((field) => field.showWhen).map((field) => field.name))
      .toEqual(["subject", "preheader", "layout", "text_source"]);
    for (const field of conditional) {
      expect(field.showWhen?.({ carries_message_fields: true })).toBe(true);
      expect(field.showWhen?.({ carries_message_fields: false })).toBe(false);
      expect(field.showWhen?.({})).toBe(false);
    }
    expect(fields.find((field) => field.name === "carries_message_fields")).toMatchObject({ hidden: true, readOnly: true });
  });

  test("shows the plain-text source for an HTML row only, and leaving HTML empties it as the server requires", () => {
    const fields = parsePageFields(children(formDeclaration()));
    const textSource = fields.find((field) => field.name === "text_source");
    const format = fields.find((field) => field.name === "format");

    // The form holds the enum as read (HTML) or as its option chose it (html).
    expect(textSource?.showWhen?.({ format: "HTML" })).toBe(true);
    expect(textSource?.showWhen?.({ format: "html" })).toBe(true);
    expect(textSource?.showWhen?.({ format: "MARKDOWN" })).toBe(false);
    expect(textSource?.showWhen?.({})).toBe(false);
    const loaded = { format: "HTML", text_source: "{{ text }}" };
    expect(format?.prefill?.("markdown", loaded, loaded)).toEqual({ text_source: "" });
    // HTML sets nothing: the form gives the field it shows again its loaded value.
    expect(format?.prefill?.("html", { format: "html" }, loaded)).toEqual({});
    expect(format?.description).toBe(M["field.formatHint"]);
  });

  test("lifts no field above the sheet as the record's body", () => {
    const fields = parsePageFields(children(formDeclaration()));

    expect(fields.filter((field) => field.body !== undefined).map((field) => [field.name, field.body])).toEqual([
      ["source", false], ["text_source", false], ["description", false],
    ]);
    expect(fields.filter((field) => field.widget === "textarea").every((field) => field.body === false)).toBe(true);
  });

  test("offers Override and Keep override where the server admits the reader, on a row in the state each changes", () => {
    const actions = parsePageActions(children(formDeclaration()));
    const override = actions.find((action) => action.id === "override");
    const keep = actions.find((action) => action.id === "keep-override");

    // The server's answer is the gate: no projected permission stands in for it.
    expect(actions.map((action) => [action.id, action.label, action.permission])).toEqual([
      ["override", M["action.override"], undefined], ["keep-override", M["action.keepOverride"], undefined],
    ]);
    expect(override?.visibleWhen?.({ can_override: true, is_overridden: false })).toBe(true);
    expect(override?.visibleWhen?.({ can_override: true, is_overridden: true })).toBe(false);
    expect(override?.visibleWhen?.({
      can_override: false, is_baseline: true, is_overridden: false, permissions: ["preview"],
    })).toBe(false);
    expect(keep?.visibleWhen?.({ can_keep_override: true, has_updated_default: true })).toBe(true);
    expect(keep?.visibleWhen?.({ can_keep_override: true, has_updated_default: false })).toBe(false);
    expect(keep?.visibleWhen?.({
      can_keep_override: false, has_updated_default: true, overrides_default: true, permissions: ["write"],
    })).toBe(false);
  });

  test("shows Delete where the row projects delete", () => {
    const form = formDeclaration();

    expect(form.deleteVisibleWhen?.({ permissions: ["delete", "preview", "write"] })).toBe(true);
    expect(form.deleteVisibleWhen?.({ permissions: ["preview"] })).toBe(false);
  });

  test("reads the row facts it shows beside its caller's selection, and puts its Preview pane before its caller's panes", () => {
    const extra: RecordTabDescriptor = { id: "extra", label: "Extra", render: () => null };
    const form = formDeclaration({ returning: ["display_name"], recordTabs: [extra] });

    expect(form.returning).toEqual([
      "display_name", "is_baseline", "is_overridden", "overrides_default", "has_updated_default", "can_override",
      "can_keep_override",
    ]);
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

  const override = { is_baseline: false, overrides_default: true };
  test.each<[string, Record<string, unknown>, readonly Sentence[]]>([
    ["a shipped default nobody overrides, to an administrator",
      { is_baseline: true, is_overridden: false, can_override: true, permissions: shipped },
      ["notice.shipped", "notice.shippedOverride"]],
    ["a shipped default nobody overrides, to a reader who may preview it but whom the server does not admit to Override",
      { is_baseline: true, is_overridden: false, can_override: false, permissions: shipped }, ["notice.shipped"]],
    ["an overridden shipped default, to an administrator",
      { is_baseline: true, is_overridden: true, can_override: true, permissions: shipped }, ["notice.overridden"]],
    ["an override whose default changed, to an administrator",
      { ...override, has_updated_default: true, can_keep_override: true, permissions: writable },
      ["notice.defaultUpdated", "notice.keepHint", "notice.deleteNew"]],
    ["a current override, to an administrator",
      { ...override, has_updated_default: false, can_keep_override: true, permissions: writable },
      ["notice.live", "notice.deleteDefault"]],
    ["the deployment's own row, to an administrator",
      { is_baseline: false, overrides_default: false, has_updated_default: false, permissions: writable },
      ["notice.live"]],
    ["a shipped default nobody overrides, to a member",
      { is_baseline: true, is_overridden: false, can_override: false, permissions: [] }, ["notice.shipped"]],
    ["an override whose default changed, to a member",
      { ...override, has_updated_default: true, can_keep_override: false, permissions: [] },
      ["notice.defaultUpdated"]],
  ])("says what %s is, and each hint only where the server admits the reader to its verb", (_case, record, shown) => {
    const { role, text } = notice(record);

    expect(role).toBe("status");
    // "notice.overridden" restates "notice.shipped" before its own sentence; the other sentences are each their own.
    const contained = SENTENCES.filter((key) => text.includes(M[key]!));
    expect(contained.filter((key) => key !== "notice.shipped" || !shown.includes("notice.overridden")))
      .toEqual(SENTENCES.filter((key) => shown.includes(key)));
  });

  test("says nothing to a member about a row whose only sentences are a writer's", () => {
    expect(notice({ ...override, has_updated_default: false, can_keep_override: false, permissions: [] }))
      .toEqual({ role: null, text: "" });
  });
});
