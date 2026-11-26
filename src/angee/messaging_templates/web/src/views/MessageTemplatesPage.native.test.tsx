// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useMemo } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { operationDocuments } from "@angee/gql/console/actions";
import { RuntimeFixture, type GraphQLWireRequest } from "@angee/storybook/testing";
import { RelatedPane } from "@angee/ui";
import { enUiMessages as UI } from "@angee/ui/i18n/en";
import { createUiTestProviders } from "@angee/ui/testing";

import {
  KEEP_REFUSAL, MESSAGE_TEMPLATE_RESOURCE, RECORDED, ROW, messageTemplateRecordContext, messageTemplateRuntime,
  messageTemplateSchemas, type MessageTemplateFixtureCall, type MessageTemplateViewer,
} from "../fixtures.test-support";
import { enMessagingTemplatesMessages as M } from "../i18n";
import { PREVIEW_PAUSE_MS } from "./MessageTemplatePreviewPane";
import { MessageTemplateStory } from "./MessageTemplatesPage.stories";

// A test that holds the pause takes the fake clock, which also runs with real time so the waits below still poll.
afterEach(() => { vi.useRealTimers(); });

/** One of the fragment's own words, as its bundle has it. */
function word(key: string): string {
  const text = M[key];
  if (text === undefined) throw new Error(`The messaging_templates bundle has no ${key}.`);
  return text;
}

/** One of the shared UI's words, as its bundle has it. */
function ui(key: string): string {
  const text = UI[key];
  if (text === undefined) throw new Error(`The ui bundle has no ${key}.`);
  return text;
}

/** The record form: the element holding the sheet, its first section titled as the fragment titles it. */
async function recordForm(): Promise<HTMLFormElement> {
  const form = (await screen.findByRole("heading", { name: word("form.identity") })).closest("form");
  if (!form) throw new Error("The message template form renders no form element.");
  return form;
}

/** The sheet's section headings and field labels, in the order the form shows them; panes are outside the form. */
async function sheetOutline(): Promise<string[]> {
  return [...(await recordForm()).querySelectorAll("h2, h3, label")]
    .map((node) => node.textContent?.trim() ?? "").filter(Boolean);
}

/** Choose one option of a select the form or the pane labels. */
async function choose(label: string, option: string): Promise<void> {
  const trigger = await screen.findByRole("combobox", { name: label });
  // A select whose options still load is busy and takes no choice.
  await waitFor(() => expect(trigger.getAttribute("aria-busy")).toBeNull());
  fireEvent.click(trigger);
  const item = await screen.findByRole("option", { name: option });
  fireEvent.pointerDown(item, { pointerType: "mouse", button: 0 });
  fireEvent.pointerUp(item, { pointerType: "mouse", button: 0 });
  fireEvent.click(item);
  await waitFor(() => expect(screen.getByRole("combobox", { name: label }).textContent).toContain(option));
}

/** Fill the create form with one identity, choosing the kind by its label in the console's metadata. */
async function fillNew({ kind, key, language, name }: Readonly<Record<string, string>>): Promise<void> {
  const kindLabel = MESSAGE_TEMPLATE_RESOURCE.fields?.find((field) => field.name === "kind")?.values
    ?.find((value) => value.value.toLowerCase() === kind)?.description ?? "";
  fireEvent.change(await screen.findByRole("textbox", { name: "Name" }), { target: { value: name } });
  await choose(word("field.kind"), kindLabel);
  fireEvent.change(screen.getByLabelText(word("field.key")), { target: { value: key } });
  fireEvent.change(screen.getByLabelText(word("field.language")), { target: { value: language } });
}

/** The calls the fake answered for one root field, by their arguments. */
function argsOf(calls: readonly MessageTemplateFixtureCall[], field: string): readonly Readonly<Record<string, unknown>>[] {
  return calls.filter((call) => call.field === field).map((call) => call.args);
}

const IDENTITY = ["form.identity", "field.kind", "field.key", "field.language"];
const MESSAGE = ["form.message", "field.subject", "field.preheader", "field.layout"];
const AFTER_CONTENT = ["form.variables", "field.variables", "form.details", "field.description"];

test.each([
  // A Markdown template: its plain text is rendered from its Markdown, so it has no plain-text source.
  ["a Markdown template override", ROW.passwordReset,
    [...IDENTITY, ...MESSAGE, "form.content", "field.format", "field.source", ...AFTER_CONTENT]],
  // An HTML block: no Message section, and its plain-text source beside its HTML.
  ["an HTML block", ROW.notice,
    [...IDENTITY, "form.content", "field.format", "field.source", "field.textSource", ...AFTER_CONTENT]],
])("%s shows its identity first, then each section in its place, and no empty section", async (_case, open, outline) => {
  render(<MessageTemplateStory open={open} />);
  await screen.findByLabelText(word("field.description"));
  expect(await sheetOutline()).toEqual(outline.map(word));
});

test("New shows only the name and the identity, sends those four, and opens the seeded row with its identity fixed", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  render(<MessageTemplateStory open="new" onCall={(call) => calls.push(call)} />);
  expect(await sheetOutline()).toEqual(IDENTITY.map(word));
  expect(screen.queryByRole("tab", { name: word("tab.preview") })).toBeNull();
  expect(screen.queryByText(word("form.sample"))).toBeNull();
  const recorded = RECORDED.insert.find((entry) => "changes" in entry && entry.object.kind === "template");
  if (!recorded || !("changes" in recorded)) throw new Error("The recorded fixtures have no new template.");
  await fillNew(recorded.object);
  fireEvent.click(screen.getByRole("button", { name: ui("form.create") }));

  await waitFor(() => expect(argsOf(calls, "insert_message_templates_one")).toHaveLength(1));
  // The app sends the insert's idempotency_key through the create targets it derives from resource metadata; the
  // shared story fixture derives none, so the key is not on this request (debt: the fixture's create targets).
  expect(argsOf(calls, "insert_message_templates_one")[0]?.object).toEqual(recorded.object);
  const seeded = recorded.changes.rows[0]!;
  expect(await screen.findByDisplayValue(String(seeded.source))).toBeTruthy();
  expect(await screen.findByDisplayValue(String(seeded.subject))).toBeTruthy();
  expect(argsOf(calls, "message_templates_by_pk").map((args) => args.id)).toContain(seeded.id);
  expect(screen.queryByRole("combobox", { name: word("field.kind") })).toBeNull();
  expect(screen.queryByRole("textbox", { name: word("field.key") })).toBeNull();
  expect(screen.queryByRole("textbox", { name: word("field.language") })).toBeNull();
  expect(screen.getByText(String(seeded.key))).toBeTruthy();
});

/** The recorded inserts the server refused as a whole: the identity, and the banner's words. */
function refusedIdentities(): [string, Readonly<Record<string, string>>, string][] {
  return RECORDED.insert.flatMap((entry): [string, Readonly<Record<string, string>>, string][] =>
    "error" in entry && entry.error.extensions.formErrors?.length
      ? [[String(entry.object.key), entry.object, entry.error.extensions.formErrors.join(" ")]]
      : []);
}

// A shipped default's identity, one the deployment has, and a new language seeded from a row whose includes nest
// past the depth: each refused before any field, in the banner, which takes focus.
test.each(refusedIdentities())("New refuses %s in the Save failed banner, in the server's words, and focuses it", async (
  _key, object, message,
) => {
  render(<MessageTemplateStory open="new" />);
  await fillNew(object);
  fireEvent.click(screen.getByRole("button", { name: ui("form.create") }));
  await screen.findByText(ui("form.saveFailed"));
  expect(await screen.findByText(message)).toBeTruthy();
  await waitFor(() => expect(document.activeElement?.getAttribute("role")).toBe("alert"));
  expect(document.activeElement?.textContent).toContain(message);
  expect(screen.getByRole("textbox", { name: "Name" })).toBeTruthy();
});

/** Whether a control is marked invalid and described by the given words, as the form shows a field's error. */
async function expectFieldError(control: HTMLElement, words: string): Promise<void> {
  await waitFor(() => expect(control.getAttribute("aria-invalid")).toBe("true"));
  const described = (control.getAttribute("aria-describedby") ?? "").split(" ")
    .map((id) => document.getElementById(id)?.textContent ?? "");
  expect(described.some((text) => text.includes(words))).toBe(true);
}

test("New shows a key the server refuses under the key", async () => {
  const refused = RECORDED.insert.find((entry) => "error" in entry && entry.error.extensions.validationErrors?.key);
  if (!refused || !("error" in refused)) throw new Error("The recorded fixtures have no refused key.");
  render(<MessageTemplateStory open="new" />);
  await fillNew(refused.object);
  fireEvent.click(screen.getByRole("button", { name: ui("form.create") }));
  await expectFieldError(screen.getByLabelText(word("field.key")), refused.error.extensions.validationErrors!.key![0]!);
});

test("Override on a shipped default sends its id and lands on the deployment's override", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  render(<MessageTemplateStory open={ROW.welcome} onCall={(call) => calls.push(call)} />);
  await screen.findByText(word("notice.shipped"));
  expect(screen.queryByRole("button", { name: word("action.keepOverride") })).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: word("action.override") }));

  await waitFor(() => expect(argsOf(calls, "override_message_template")).toEqual([{ template: ROW.welcome }]));
  const result = RECORDED.override[ROW.welcome]!.result;
  expect((await screen.findAllByText(result.message)).length).toBeGreaterThan(0);
  await waitFor(() => expect(argsOf(calls, "message_templates_by_pk").map((args) => args.id)).toContain(result.id));
  expect(await screen.findByText(word("notice.live"), { exact: false })).toBeTruthy();
  expect(screen.queryByText(word("notice.shipped"), { exact: false })).toBeNull();
  expect(screen.queryByRole("button", { name: word("action.override") })).toBeNull();
});

/** The toasts a person sees, one element each, in the toast library's viewport (named Notifications by it). */
function shownToasts(): HTMLElement[] {
  return [...screen.getByRole("region", { name: "Notifications" })
    .querySelectorAll<HTMLElement>("[role=dialog], [role=alertdialog]")];
}

test("Keep override sends the override's id, toasts the server's message once, and the default-updated warning goes", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  render(<MessageTemplateStory open={ROW.recordCard} onCall={(call) => calls.push(call)} />);
  await screen.findByText(word("notice.defaultUpdated"));
  fireEvent.click(await screen.findByRole("button", { name: word("action.keepOverride") }));

  await waitFor(() => expect(argsOf(calls, "keep_message_template_override")).toEqual([{ template: ROW.recordCard }]));
  await waitFor(() => expect(screen.queryByText(word("notice.defaultUpdated"), { exact: false })).toBeNull());
  expect(await screen.findByText(word("notice.live"), { exact: false })).toBeTruthy();
  expect(screen.queryByRole("button", { name: word("action.keepOverride") })).toBeNull();
  // By now the verb has settled and the row has been read again: one toast, the server's message.
  expect(shownToasts().map((toast) => toast.textContent)).toEqual([RECORDED.keep[ROW.recordCard]!.result.message]);
});

test("a reader who may preview a shipped default but whom the server does not admit to Override is offered neither", async () => {
  render(<MessageTemplateStory open={ROW.welcome} overrideDenied />);
  await screen.findByText(word("notice.shipped"));
  // The reader holds preview: the Preview tab is there, and only the server's answer hides Override.
  expect(await screen.findByRole("tab", { name: word("tab.preview") })).toBeTruthy();
  expect(screen.queryByRole("button", { name: word("action.override") })).toBeNull();
  expect(screen.queryByText(word("notice.shippedOverride"), { exact: false })).toBeNull();
});

test("a current override offers no Keep override, and says Delete returns to the shipped default", async () => {
  render(<MessageTemplateStory open={ROW.quote} />);
  expect(await screen.findByText(word("notice.deleteDefault"), { exact: false })).toBeTruthy();
  expect(screen.getByText(word("notice.live"), { exact: false })).toBeTruthy();
  expect(screen.queryByRole("button", { name: word("action.keepOverride") })).toBeNull();
  expect(screen.queryByRole("button", { name: word("action.override") })).toBeNull();
});

test("a refused Keep override says why under the verb's name, in the server's words for this row, and the warning stays", async () => {
  render(<MessageTemplateStory open={ROW.recordCard} refuseKeep />);
  await screen.findByText(word("notice.defaultUpdated"));
  fireEvent.click(await screen.findByRole("button", { name: word("action.keepOverride") }));

  // The action bar toasts the refusal once, titled with the verb, its body the server's reason; the toast is
  // also announced in a live region, so its words are on the page more than once.
  const reasons = Object.values(KEEP_REFUSAL.validation_errors ?? {}).flat().map(String);
  expect(reasons.length).toBeGreaterThan(0);
  for (const reason of reasons) {
    expect((await screen.findAllByText(reason, { exact: false })).length).toBeGreaterThan(0);
  }
  expect(shownToasts()).toHaveLength(1);
  expect(screen.getAllByText(word("action.keepOverride")).length).toBeGreaterThan(1);
  // The server's summary gives way to its reasons: no second toast says it.
  expect(screen.queryByText(KEEP_REFUSAL.message, { exact: false })).toBeNull();
  expect(screen.getByText(word("notice.defaultUpdated"))).toBeTruthy();
});

test("Delete on a block another row includes shows the server's refusal", async () => {
  render(<MessageTemplateStory open={ROW.notice} />);
  await screen.findByText(word("notice.live"), { exact: false });
  // The deployment's own block overrides no shipped default, so its notice names none.
  expect(screen.queryByText(word("notice.deleteDefault"), { exact: false })).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: ui("list.actions") }));
  fireEvent.click(await screen.findByRole("menuitem", { name: ui("actions.delete") }));

  const refusal = RECORDED.delete[ROW.notice]!.preview.refusals[0]!;
  expect(await screen.findByText(refusal)).toBeTruthy();
});

test("a save the server refuses shows its row-wide reasons in the Save failed banner and each field's under the field", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  const refusal = RECORDED.save.layout;
  render(<MessageTemplateStory open={ROW.passwordReset} refuseSave="layout" onCall={(call) => calls.push(call)} />);
  // The list's kind groups share their names with fields ("Layout"), so the field is looked up in the form.
  const form = await recordForm();
  await within(form).findByLabelText(word("field.layout"));
  await choose(word("field.format"), "HTML");
  fireEvent.change(within(form).getByLabelText(word("field.layout")), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: ui("form.save") }));

  await screen.findByText(ui("form.saveFailed"));
  expect(await screen.findByText(refusal.error.extensions.formErrors!.join(" "))).toBeTruthy();
  for (const words of refusal.error.extensions.validationErrors?.layout ?? []) {
    await expectFieldError(within(form).getByLabelText(word("field.layout")), words);
  }
  // A refusal that carries a reason no field shows brings the person to the banner, which holds that reason and
  // names the refused field (the record form's rule).
  await waitFor(() => expect(document.activeElement?.getAttribute("role")).toBe("alert"));
  expect(document.activeElement?.textContent).toContain(refusal.error.extensions.formErrors![0]);
  expect(document.activeElement?.textContent).toContain(word("field.layout"));
  expect(argsOf(calls, "update_message_templates_by_pk").map((args) => args._set)).toEqual([refusal.set]);
});

test("a declared path or description over its length is refused at the variables editor, which takes focus", async () => {
  for (const why of ["variablePath", "variableDescription"] as const) {
    const words = RECORDED.save[why].error.extensions.validationErrors!.variables![0]!;
    render(<MessageTemplateStory open={ROW.quote} refuseSave={why} />);
    const form = await recordForm();
    // The editor types through its code editor; the save is answered as the server refused that value.
    fireEvent.change(within(form).getByLabelText(word("field.description")), { target: { value: "Edited." } });
    fireEvent.click(screen.getByRole("button", { name: ui("form.save") }));
    const field = (await within(form).findByText(word("field.variables"))).parentElement!.parentElement!;
    expect(await within(field).findByText(words)).toBeTruthy();
    await waitFor(() => expect(field.contains(document.activeElement)).toBe(true));
    cleanup();
  }
});

test("a source pasted with carriage returns saves as the server keeps it, and Default updated stays off", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  const pasted = RECORDED.saved.find((entry) => entry.id === ROW.passwordReset)!;
  render(<MessageTemplateStory open={ROW.passwordReset} onCall={(call) => calls.push(call)} />);
  const form = await recordForm();
  fireEvent.change(await within(form).findByLabelText(word("field.source")), { target: { value: pasted.set.source } });
  fireEvent.click(screen.getByRole("button", { name: ui("form.save") }));

  await screen.findByText(ui("form.updateSuccess"));
  expect(argsOf(calls, "update_message_templates_by_pk").map((args) => args._set)).toEqual([pasted.set]);
  // The server keeps newlines, and the row still overrides a current default.
  await waitFor(() => expect(within(form).getByLabelText<HTMLTextAreaElement>(word("field.source")).value)
    .toBe(pasted.reread.source));
  expect(screen.queryByText(word("notice.defaultUpdated"), { exact: false })).toBeNull();
  expect(screen.getByText(word("notice.live"), { exact: false })).toBeTruthy();
});

test("an override whose default changed still says so after a save and the read that follows it", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  const kept = RECORDED.saved.find((entry) => entry.id === ROW.recordCard)!;
  render(<MessageTemplateStory open={ROW.recordCard} onCall={(call) => calls.push(call)} />);
  await screen.findByText(word("notice.defaultUpdated"));
  const reads = argsOf(calls, "message_templates_by_pk").length;
  fireEvent.change(screen.getByLabelText(word("field.description")), { target: { value: kept.set.description } });
  fireEvent.click(screen.getByRole("button", { name: ui("form.save") }));

  await screen.findByText(ui("form.updateSuccess"));
  expect(argsOf(calls, "update_message_templates_by_pk").map((args) => args._set)).toEqual([kept.set]);
  // The form reads the row again after the save, as a reload does, and the server still says the default changed.
  await waitFor(() => expect(argsOf(calls, "message_templates_by_pk").length).toBeGreaterThan(reads));
  expect(kept.reread.has_updated_default).toBe(true);
  expect(screen.getByText(word("notice.defaultUpdated"))).toBeTruthy();
});

test("a template whose include sits inside a condition renders with that value empty", async () => {
  const preview = RECORDED.previews[ROW.conditionalInclude]!;
  const row = RECORDED.rows.find((candidate) => candidate.id === ROW.conditionalInclude)!;
  expect(Object.keys(row.sample as object)).not.toContain("booking_url");
  expect(preview.rendered).not.toBeNull();
  render(<MessageTemplateStory open={ROW.conditionalInclude} />);
  const frame = await screen.findByTitle(word("preview.frameTitle"));
  await waitFor(() => expect(frame.getAttribute("srcdoc")).toContain(preview.rendered!.html));
  expect(screen.queryByText(word("preview.refused"))).toBeNull();
  // Nor does it warn: the band lists no warning, only what it always says of a rendered mail.
  expect(preview.warnings).toEqual([]);
  expect(bandOutline(screen.getByRole("region", { name: word("preview.factsRegion") }), []))
    .toEqual(["saved", "message", "caveat"]);
});

/** The plain-text source in the record form, or null where the form does not show it. */
function plainTextSource(form: HTMLElement): HTMLTextAreaElement | null {
  return within(form).queryByLabelText<HTMLTextAreaElement>(word("field.textSource"));
}

test("an HTML row's plain-text source goes with Markdown, comes back as loaded with HTML, and a Markdown save empties it", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  const loaded = String(RECORDED.rows.find((row) => row.id === ROW.quote)!.text_source);
  render(<MessageTemplateStory open={ROW.quote} onCall={(call) => calls.push(call)} />);
  const form = await recordForm();
  await within(form).findByLabelText(word("field.textSource"));
  expect(plainTextSource(form)?.value).toBe(loaded);
  // What the Format hint says: an unsaved edit does not survive the round trip, the loaded text comes back.
  fireEvent.change(plainTextSource(form)!, { target: { value: "An edit nobody saved." } });
  await choose(word("field.format"), "Markdown");
  await waitFor(() => expect(plainTextSource(form)).toBeNull());
  await choose(word("field.format"), "HTML");
  await waitFor(() => expect(plainTextSource(form)?.value).toBe(loaded));
  await choose(word("field.format"), "Markdown");
  fireEvent.click(screen.getByRole("button", { name: ui("form.save") }));

  await screen.findByText(ui("form.updateSuccess"));
  expect(argsOf(calls, "update_message_templates_by_pk").map((args) => args._set))
    .toEqual([{ format: "markdown", text_source: "" }]);
});

test("a Markdown row switched to HTML shows an empty plain-text source and saves its format alone", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  render(<MessageTemplateStory open={ROW.passwordReset} onCall={(call) => calls.push(call)} />);
  const form = await recordForm();
  await within(form).findByLabelText(word("field.description"));
  expect(plainTextSource(form)).toBeNull();
  await choose(word("field.format"), "HTML");
  await waitFor(() => expect(plainTextSource(form)?.value).toBe(""));
  fireEvent.click(screen.getByRole("button", { name: ui("form.save") }));

  // The fake saves what it is sent; the server would ask for the plain-text source (the refused save above).
  await screen.findByText(ui("form.updateSuccess"));
  expect(argsOf(calls, "update_message_templates_by_pk").map((args) => args._set)).toEqual([{ format: "html" }]);
});

test("choosing a brand kit sends its id as brand_kit, and the default kit sends none", async () => {
  const requests: GraphQLWireRequest[] = [];
  render(<MessageTemplateStory open={ROW.welcome} onRequest={(request) => requests.push(request)} />);
  await screen.findByTitle(word("preview.frameTitle"));
  const previews = () => requests.filter((request) => request.operationName === "MessageTemplatePreview")
    .map((request) => request.variables);
  expect(previews()[0]).toEqual({ template: ROW.welcome });
  const other = RECORDED.kits.find((kit) => kit.is_default !== true)!;
  await choose(word("preview.kit"), String(other.display_name));

  await waitFor(() => expect(previews().at(-1)).toEqual({ template: ROW.welcome, brand_kit: other.id }));
  const inKit = RECORDED.previewsInKit[other.id]![ROW.welcome]!.rendered!.html;
  await waitFor(() => expect(screen.getByTitle(word("preview.frameTitle")).getAttribute("srcdoc")).toContain(inKit));
});

test("the preview's HTML is in the frame's document and never a node of the page, beside the frame or anywhere", async () => {
  render(<MessageTemplateStory open={ROW.welcome} />);
  const frame = await screen.findByTitle(word("preview.frameTitle"));
  const rendered = RECORDED.previews[ROW.welcome]!.rendered!;
  // The layout's article wrapper and a sentence only the render writes: both are the frame's alone.
  expect(rendered.html).toContain('aria-roledescription="email"');
  expect(rendered.html).toContain("Your Angee account is ready");
  await waitFor(() => expect(frame.getAttribute("srcdoc")).toContain(rendered.html));
  expect(document.querySelector("[aria-roledescription=email]")).toBeNull();
  expect(document.body.textContent).not.toContain("Your Angee account is ready");
});

test("the Arabic template reads right to left in its frame, its subject, its preheader and its plain text", async () => {
  const rendered = RECORDED.previews[ROW.welcomeArabic]!.rendered!;
  expect(rendered.direction).toBe("RTL");
  render(<MessageTemplateStory open={ROW.welcomeArabic} />);
  const frame = await screen.findByTitle(word("preview.frameTitle"));
  await waitFor(() => expect(frame.getAttribute("srcdoc")).toContain('dir="rtl"'));
  expect(frame.getAttribute("srcdoc")).toContain('<html lang="ar"');
  // The direction the server rendered in, not a guess from the first letter: the subject starts in Arabic and
  // carries the Latin brand name, and the plain text starts with that Latin name.
  for (const text of [rendered.subject, rendered.preheader]) {
    const span = within(frame.parentElement!.parentElement!).getByText(text);
    expect([span.getAttribute("dir"), span.getAttribute("lang")]).toEqual(["rtl", "ar"]);
  }
  fireEvent.click(within(await previewToolbar()).getByRole("button", { name: word("preview.text") }));
  const plain = await screen.findByRole("region", { name: word("preview.text") });
  expect([plain.getAttribute("dir"), plain.getAttribute("lang")]).toEqual(["rtl", "ar"]);
});

test("a left-to-right mail's subject, preheader and plain text say so, from the recorded direction", async () => {
  const rendered = RECORDED.previews[ROW.welcome]!.rendered!;
  expect(rendered.direction).toBe("LTR");
  render(<MessageTemplateStory open={ROW.welcome} />);
  const pane = (await screen.findByTitle(word("preview.frameTitle"))).parentElement!.parentElement!;
  expect(within(pane).getByText(rendered.subject).getAttribute("dir")).toBe("ltr");
  fireEvent.click(within(await previewToolbar()).getByRole("button", { name: word("preview.text") }));
  expect((await screen.findByRole("region", { name: word("preview.text") })).getAttribute("dir")).toBe("ltr");
});

/** What each of the band's children is, in its order: by the one fact of the pane each shows. */
function bandOutline(band: HTMLElement, warnings: readonly string[]): string[] {
  return [...band.children].flatMap((child): string[] => {
    const text = child.textContent ?? "";
    // The band's polite region, item by item: a refused read ("error"), then the warnings, all of them ("warnings").
    // With nothing in it, it is out of flow and shows nothing to outline.
    if (child.querySelector("[aria-live=polite]")) {
      const kinds = [...child.querySelectorAll("li")].map((item) =>
        warnings.some((detail) => item.textContent?.includes(detail)) ? "warnings" : "error");
      if (kinds.includes("warnings") && !warnings.every((detail) => text.includes(detail))) return ["some warnings"];
      return kinds.filter((kind, index) => kinds.indexOf(kind) === index);
    }
    if (text === word("preview.saved")) return ["saved"];
    if (text === word("preview.unsaved")) return ["unsaved"];
    if (text.includes(word("preview.subject")) && text.includes(word("preview.preheader"))) return ["message"];
    if (text === word("preview.fidelity")) return ["caveat"];
    return [`unknown: ${text}`];
  });
}

test("a template whose preview warns lists each warning in the server's words, first in the band", async () => {
  render(<MessageTemplateStory open={ROW.passwordReset} />);
  await screen.findByTitle(word("preview.frameTitle"));
  const band = screen.getByRole("region", { name: word("preview.factsRegion") });
  const warnings = RECORDED.previews[ROW.passwordReset]!.warnings.map((warning) => warning.detail);
  for (const detail of warnings) expect(await within(band).findByText(detail)).toBeTruthy();

  // The whole order: the warnings, the saved-row line, the subject and preheader, the frame caveat.
  expect(bandOutline(band, warnings)).toEqual(["warnings", "saved", "message", "caveat"]);
});

/** A query client that does not retry a refused read, as the app's own does not retry a GraphQL refusal. */
function noRetryClient() {
  return createUiTestProviders().createClient({ defaultOptions: { queries: { retry: false } } });
}

test("a preview read for another kit that fails shows its error and nothing of the earlier answer", async () => {
  render(<MessageTemplateStory open={ROW.passwordReset} queryClient={noRetryClient()} />);
  await screen.findByTitle(word("preview.frameTitle"));
  // The recording holds Password reset's preview in the default kit only: the other kit's read fails. It is a
  // new read, so the earlier kit's answer is not kept beside its error.
  const other = RECORDED.kits.find((kit) => kit.is_default !== true)!;
  await choose(word("preview.kit"), String(other.display_name));
  const band = screen.getByRole("region", { name: word("preview.factsRegion") });
  await waitFor(() => expect(bandOutline(band, [])[0]).toBe("error"));

  expect(bandOutline(band, [])).toEqual(["error", "saved"]);
  expect(screen.queryByTitle(word("preview.frameTitle"))).toBeNull();
});

test("a preview read again after a save that fails shows its error first, above the earlier answer it keeps", async () => {
  render(<MessageTemplateStory open={ROW.passwordReset} failPreviewAfterSave queryClient={noRetryClient()} />);
  await screen.findByTitle(word("preview.frameTitle"));
  // The save makes the preview read the same row again, and that read fails: the earlier answer stays.
  fireEvent.change(screen.getByLabelText(word("field.description")), { target: { value: "Sent on request." } });
  fireEvent.click(screen.getByRole("button", { name: ui("form.save") }));
  await screen.findByText(ui("form.updateSuccess"));
  const band = screen.getByRole("region", { name: word("preview.factsRegion") });
  await waitFor(() => expect(bandOutline(band, [])[0]).toBe("error"));
  const warnings = RECORDED.previews[ROW.passwordReset]!.warnings.map((warning) => warning.detail);

  // The whole order with every part present: the error, the warnings, the saved-row line, subject and preheader,
  // the frame caveat; the frame keeps the earlier mail.
  expect(bandOutline(band, warnings)).toEqual(["error", "warnings", "saved", "message", "caveat"]);
  expect(screen.getByTitle(word("preview.frameTitle"))).toBeTruthy();
});

test("the library's three flags read as facts, a word for each state and no checkbox", async () => {
  render(<MessageTemplateStory />);
  const open = (name: string) => ui("list.openRecord").replace("{label}", name);
  // In a routed list the row's opener is a link.
  await screen.findByRole("link", { name: open("Button") });
  const headers = screen.getAllByRole("columnheader");
  const flags = [word("col.shipped"), word("col.effective"), word("col.defaultUpdated")]
    .map((label) => headers.findIndex((header) => header.textContent === label));
  expect(flags.every((index) => index >= 0)).toBe(true);
  for (const [name, states] of [
    // A shipped block nobody overrides, and the deployment's own block.
    ["Button", ["flag.yes", "flag.yes", "flag.no"]],
    ["Notice", ["flag.no", "flag.yes", "flag.no"]],
  ] as const) {
    const row = screen.getByRole("link", { name: open(name) }).closest("tr")!;
    const cells = within(row).getAllByRole("cell");
    expect(flags.map((index) => cells[index]?.textContent)).toEqual(states.map(word));
    for (const index of flags) expect(within(cells[index]!).queryByRole("checkbox")).toBeNull();
  }
});

test("the Format line is said to a reader who may change the format, not on a read-only form", async () => {
  render(<MessageTemplateStory open={ROW.quote} />);
  expect(await screen.findByText(word("field.formatHint"))).toBeTruthy();
  cleanup();

  render(<MessageTemplateStory open={ROW.welcome} />);
  await screen.findByText(word("notice.shipped"));
  await within(await recordForm()).findByText(word("field.format"));
  expect(screen.queryByText(word("field.formatHint"))).toBeNull();
  cleanup();

  render(<MessageTemplateStory open={ROW.quote} viewer="member" />);
  await within(await recordForm()).findByText(word("field.format"));
  expect(screen.queryByText(word("field.formatHint"))).toBeNull();
});

test("a template that does not render says so first, then why in the server's words, with no frame and no frame caveat", async () => {
  render(<MessageTemplateStory open={ROW.invoiceSent} />);
  const title = await screen.findByText(word("preview.refused"));
  const reasons = RECORDED.previews[ROW.invoiceSent]!.warnings.map((warning) => screen.getByText(warning.detail));
  expect(reasons.length).toBeGreaterThan(0);
  // The state and its reasons sit together at the top of the pane's body, the state first; it scrolls from the keyboard.
  const body = screen.getByRole("region", { name: word("preview.refusedRegion") });
  expect(body.contains(title)).toBe(true);
  expect(body.className).toContain("overflow-y-auto");
  expect(body.getAttribute("tabindex")).toBe("0");
  for (const reason of reasons) {
    expect(body?.contains(reason)).toBe(true);
    expect(title.compareDocumentPosition(reason) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  }
  expect(screen.queryByTitle(word("preview.frameTitle"))).toBeNull();
  expect(screen.queryByText(word("preview.fidelity"))).toBeNull();
  expect(screen.queryByRole("group", { name: word("preview.format") })).toBeNull();
});

/** The Preview pane's toolbar: the shared pane toolbar holding the brand kit select. */
async function previewToolbar(): Promise<HTMLElement> {
  const kit = await screen.findByRole("combobox", { name: word("preview.kit") });
  const toolbar = kit.closest<HTMLElement>("[role=toolbar]");
  if (!toolbar) throw new Error("The brand kit select is in no toolbar.");
  return toolbar;
}

test("the Preview pane's brand kit and HTML or plain-text switch share its one toolbar row, which carries no title", async () => {
  render(<MessageTemplateStory open={ROW.welcome} />);
  await screen.findByTitle(word("preview.frameTitle"));
  const toolbar = await previewToolbar();

  expect(within(toolbar).getByRole("group", { name: word("preview.format") })).toBeTruthy();
  // The strip names the tab; the row holds the toolbar alone, and the saved-row caption is read under it, whole.
  expect(toolbar.parentElement?.parentElement?.textContent).not.toContain(word("preview.saved"));
  const facts = screen.getByRole("region", { name: word("preview.factsRegion") });
  expect(within(facts).getByText(word("preview.saved"))).toBeTruthy();
});

test("the frame fills the pane's body and scrolls the mail itself; what the server says takes part of the pane at most", async () => {
  render(<MessageTemplateStory open={ROW.passwordReset} />);
  const frame = await screen.findByTitle(word("preview.frameTitle"));
  const box = frame.parentElement!;
  expect(box.className).toContain("flex-1");
  expect(box.className).toContain("min-h-0");
  expect(box.className).not.toContain("min-h-96");
  // The warnings sit in a band that scrolls within its share of the pane, above the frame, and from the keyboard.
  const warning = await screen.findByText(RECORDED.previews[ROW.passwordReset]!.warnings[0]!.detail);
  const band = screen.getByRole("region", { name: word("preview.factsRegion") });
  expect(band.contains(warning)).toBe(true);
  expect(band.className).toContain("max-h-[40%]");
  expect(band.className).toContain("overflow-y-auto");
  expect(band.getAttribute("tabindex")).toBe("0");
  expect(band.contains(frame)).toBe(false);
});

test("the frame caveat shows beside a frame only, not with the plain text", async () => {
  render(<MessageTemplateStory open={ROW.welcome} />);
  await screen.findByTitle(word("preview.frameTitle"));
  expect(screen.getByText(word("preview.fidelity"))).toBeTruthy();
  fireEvent.click(within(await previewToolbar()).getByRole("button", { name: word("preview.text") }));

  expect(await screen.findByRole("region", { name: word("preview.text") })).toBeTruthy();
  expect(screen.queryByTitle(word("preview.frameTitle"))).toBeNull();
  expect(screen.queryByText(word("preview.fidelity"))).toBeNull();
});

/** The Preview pane itself: the frame's box sits in it, beneath what the server says of the render. */
async function previewPane(): Promise<HTMLElement> {
  return (await screen.findByTitle(word("preview.frameTitle"))).parentElement!.parentElement!;
}

test("a mail's Preview shows its subject and preheader; a block's has neither row", async () => {
  render(<MessageTemplateStory open={ROW.welcome} />);
  const rendered = RECORDED.previews[ROW.welcome]!.rendered!;
  const mail = await previewPane();
  expect(within(mail).getByText(word("preview.subject"))).toBeTruthy();
  expect(within(mail).getByText(rendered.subject)).toBeTruthy();
  expect(within(mail).getByText(word("preview.preheader"))).toBeTruthy();
  expect(within(mail).getByText(rendered.preheader)).toBeTruthy();
  cleanup();

  render(<MessageTemplateStory open={ROW.quote} />);
  const block = await previewPane();
  expect(within(block).queryByText(word("preview.subject"))).toBeNull();
  expect(within(block).queryByText(word("preview.preheader"))).toBeNull();
});

test("after a save, the preview reads the row again", async () => {
  const requests: GraphQLWireRequest[] = [];
  render(<MessageTemplateStory open={ROW.quote} onRequest={(request) => requests.push(request)} />);
  await screen.findByTitle(word("preview.frameTitle"));
  const reads = () => requests.filter((request) => request.operationName === "MessageTemplatePreview").length;
  const before = reads();
  fireEvent.change(screen.getByLabelText(word("field.description")), { target: { value: "Someone's words, set apart." } });
  fireEvent.click(screen.getByRole("button", { name: ui("form.save") }));

  await screen.findByText(ui("form.updateSuccess"));
  await waitFor(() => expect(reads()).toBeGreaterThan(before));
});

/** The recorded preview of the unsaved edit to Password reset that writes `column`. */
function unsavedPreview(column: string): (typeof RECORDED.unsavedPreviews)[number] {
  const recorded = RECORDED.unsavedPreviews.find((entry) => entry.id === ROW.passwordReset && column in entry.patch);
  if (!recorded) throw new Error(`The recorded fixtures preview no unsaved edit of ${column}.`);
  return recorded;
}

/** The variables of each preview read the page sent, in order. */
function previewReads(requests: readonly GraphQLWireRequest[]): Readonly<Record<string, unknown>>[] {
  return requests.filter((request) => request.operationName === "MessageTemplatePreview")
    .map((request) => request.variables as Readonly<Record<string, unknown>>);
}

/** What the server says of the render, above the body; the pane is its parent. */
function factsBand(): HTMLElement {
  return screen.getByRole("region", { name: word("preview.factsRegion") });
}

/** The Preview pane, which says when it is busy. */
function busyPane(): HTMLElement {
  return factsBand().parentElement!;
}

/**
 * Whether what the pane shows stands in for a preview not yet read: the band and the body under it dimmed and
 * inert, as a list keeping its previous rows is; the toolbar never is. Each is asked, and they must agree with each
 * other and with the pane's `aria-busy`: one state, seen and announced alike.
 */
function standsIn(): boolean {
  const body = factsBand().nextElementSibling as HTMLElement;
  const states = [factsBand(), body].map((part) => part.className.includes("opacity-50")
    && part.className.includes("pointer-events-none"));
  expect(states[0]).toBe(states[1]);
  expect(busyPane().getAttribute("aria-busy") === "true").toBe(states[0]);
  return states[0]!;
}

/** The pane's two polite regions: the warnings' in the band, and the reasons' at the end of the pane's column. */
function politeRegions(): [HTMLElement | null, HTMLElement | null] {
  return [
    factsBand().querySelector<HTMLElement>("[aria-live=polite]"),
    busyPane().lastElementChild?.querySelector<HTMLElement>("[aria-live=polite]") ?? null,
  ];
}

/** The text of what describes a control (`aria-describedby`). */
function describedBy(control: HTMLElement): string {
  return (control.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? "").join(" ");
}

/** Let the open form's edits settle, as a pause after the last keystroke does. */
function pause(): void {
  act(() => { vi.advanceTimersByTime(PREVIEW_PAUSE_MS); });
}

/** A preview answer a test holds, and releases when it chooses. */
function heldAnswer(): { promise: Promise<void>; release: () => void } {
  let release = () => {};
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

/** The edits a held preview read carries, as text whatever the order of their columns. */
function editsKey(patch: unknown): string {
  return JSON.stringify(Object.entries(patch as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right)));
}

/**
 * Replace the whole text of a code editor the form labels, as a person does: select all, then paste. The paste
 * lands once focus has left the editor, which then writes no selection into the page while it updates: the test
 * document announces a selection change at once, inside the editor's own update, where a browser waits.
 */
function pasteInto(editor: HTMLElement, text: string): void {
  act(() => editor.focus());
  const mac = /Mac/.test(navigator.platform);
  fireEvent.keyDown(editor, { key: "a", code: "KeyA", metaKey: mac, ctrlKey: !mac });
  act(() => editor.blur());
  fireEvent.paste(editor, { clipboardData: { getData: (type: string) => (type === "text/plain" ? text : "") } });
}

test("an edit is read once it settles, as its Save would send it, under the caption for unsaved edits", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const requests: GraphQLWireRequest[] = [];
  const edit = unsavedPreview("subject");
  const rendered = edit.result.rendered!;
  render(<MessageTemplateStory open={ROW.passwordReset} onRequest={(request) => requests.push(request)} />);
  const form = await recordForm();
  await screen.findByTitle(word("preview.frameTitle"));
  const before = previewReads(requests).length;
  fireEvent.change(within(form).getByLabelText(word("field.subject")), { target: { value: edit.patch.subject } });

  // While the edit settles nothing is read: the saved row's preview stays, under its own caption, said to be busy.
  expect(previewReads(requests)).toHaveLength(before);
  expect(within(factsBand()).getByText(word("preview.saved"))).toBeTruthy();
  expect(busyPane().getAttribute("aria-busy")).toBe("true");
  pause();
  await waitFor(() => expect(previewReads(requests).slice(before))
    .toEqual([{ template: ROW.passwordReset, patch: edit.patch }]));
  const subject = await within(factsBand()).findByText(rendered.subject);
  // The edits' subject reads in the direction the server rendered them in, as the saved row's does.
  expect([subject.getAttribute("dir"), subject.getAttribute("lang")]).toEqual(["ltr", rendered.language]);
  const warnings = edit.result.warnings.map((warning) => warning.detail);
  expect(bandOutline(factsBand(), warnings)).toEqual(["warnings", "unsaved", "message", "caveat"]);
  await waitFor(() => expect(screen.getByTitle(word("preview.frameTitle")).getAttribute("srcdoc")).toContain(rendered.html));
  await waitFor(() => expect(busyPane().getAttribute("aria-busy")).toBeNull());
  expect(standsIn()).toBe(false);

  // A second edit settles over the first one's preview: that stays, under the unsaved caption, dimmed, unread.
  const reads = previewReads(requests).length;
  fireEvent.change(within(form).getByLabelText(word("field.layout")), { target: { value: "" } });
  expect(within(factsBand()).getByText(word("preview.unsaved"))).toBeTruthy();
  expect(within(factsBand()).getByText(rendered.subject)).toBeTruthy();
  expect(standsIn()).toBe(true);
  expect(busyPane().getAttribute("aria-busy")).toBe("true");
  expect(previewReads(requests)).toHaveLength(reads);
});

test("a kit chosen while the form holds edits reads those edits in that kit", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const requests: GraphQLWireRequest[] = [];
  const edit = unsavedPreview("subject");
  render(<MessageTemplateStory open={ROW.passwordReset} onRequest={(request) => requests.push(request)}
    queryClient={noRetryClient()} />);
  const form = await recordForm();
  await screen.findByTitle(word("preview.frameTitle"));
  fireEvent.change(within(form).getByLabelText(word("field.subject")), { target: { value: edit.patch.subject } });
  pause();
  await within(factsBand()).findByText(edit.result.rendered!.subject);
  const other = RECORDED.kits.find((kit) => kit.is_default !== true)!;
  await choose(word("preview.kit"), String(other.display_name));

  // The recording holds no preview of these edits in that kit, so the fake refuses it; the read is what counts.
  await waitFor(() => expect(previewReads(requests).at(-1))
    .toEqual({ template: ROW.passwordReset, brand_kit: other.id, patch: edit.patch }));
});

test("Discard returns the Preview to the saved row at once, and the next edit starts from it", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const requests: GraphQLWireRequest[] = [];
  const edit = unsavedPreview("subject");
  const saved = RECORDED.previews[ROW.passwordReset]!.rendered!;
  render(<MessageTemplateStory open={ROW.passwordReset} onRequest={(request) => requests.push(request)} />);
  const form = await recordForm();
  await screen.findByTitle(word("preview.frameTitle"));
  fireEvent.change(within(form).getByLabelText(word("field.subject")), { target: { value: edit.patch.subject } });
  pause();
  await within(factsBand()).findByText(edit.result.rendered!.subject);
  fireEvent.click(screen.getByRole("button", { name: ui("form.discard") }));

  // In the same turn: the saved row's caption and its preview, read before the edit, with no pause between.
  expect(within(factsBand()).getByText(word("preview.saved"))).toBeTruthy();
  expect(within(factsBand()).getByText(saved.subject)).toBeTruthy();
  expect(previewReads(requests).filter((variables) => "patch" in variables)).toEqual([
    { template: ROW.passwordReset, patch: edit.patch },
  ]);
  // The next edit starts from the saved row: while it settles, the saved row shows, never the discarded edits.
  fireEvent.change(within(form).getByLabelText(word("field.layout")), { target: { value: "" } });
  expect(within(factsBand()).getByText(word("preview.saved"))).toBeTruthy();
  expect(within(factsBand()).getByText(saved.subject)).toBeTruthy();
  expect(within(factsBand()).queryByText(edit.result.rendered!.subject)).toBeNull();
});

test("after a save the Preview shows the saved row at once, busy and dimmed until the row is read again", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const requests: GraphQLWireRequest[] = [];
  const reread = heldAnswer();
  let saved = false;
  const edit = unsavedPreview("subject");
  const before = RECORDED.previews[ROW.passwordReset]!.rendered!;
  render(<MessageTemplateStory open={ROW.passwordReset} onRequest={(request) => requests.push(request)}
    onCall={(call) => { if (call.field === "update_message_templates_by_pk") saved = true; }}
    beforePreview={(args) => (saved && args.patch == null ? reread.promise : undefined)} />);
  const form = await recordForm();
  await screen.findByTitle(word("preview.frameTitle"));
  fireEvent.change(within(form).getByLabelText(word("field.subject")), { target: { value: edit.patch.subject } });
  pause();
  await within(factsBand()).findByText(edit.result.rendered!.subject);
  fireEvent.click(screen.getByRole("button", { name: ui("form.save") }));
  await screen.findByText(ui("form.updateSuccess"));

  // The saved row's earlier answer is the render from before the edit: it shows only said to be busy.
  await waitFor(() => expect(within(factsBand()).getByText(word("preview.saved"))).toBeTruthy());
  expect(within(factsBand()).getByText(before.subject)).toBeTruthy();
  expect(busyPane().getAttribute("aria-busy")).toBe("true");
  // The render from before the edits, under the saved row's caption: busy and dimmed until the row is read again.
  expect(standsIn()).toBe(true);
  act(() => reread.release());
  await waitFor(() => expect(busyPane().getAttribute("aria-busy")).toBeNull());
  expect(standsIn()).toBe(false);
  expect(previewReads(requests).at(-1)).toEqual({ template: ROW.passwordReset });
});

test("answers released out of order never show a preview of edits the form no longer holds", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const held = new Map<string, ReturnType<typeof heldAnswer>>();
  const subject = unsavedPreview("subject");
  const refused = unsavedPreview("layout");
  const savedSubject = String(RECORDED.rows.find((row) => row.id === ROW.passwordReset)!.subject);
  render(<MessageTemplateStory open={ROW.passwordReset} beforePreview={(args) => {
    if (args.patch == null) return undefined;
    const answer = heldAnswer();
    held.set(editsKey(args.patch), answer);
    return answer.promise;
  }} />);
  const form = await recordForm();
  await screen.findByTitle(word("preview.frameTitle"));
  const subjectBox = within(form).getByLabelText(word("field.subject"));
  // First the subject, read and held.
  fireEvent.change(subjectBox, { target: { value: subject.patch.subject } });
  pause();
  await waitFor(() => expect(held.has(editsKey(subject.patch))).toBe(true));
  // Then the subject put back and the edits Save would refuse, read and held too.
  fireEvent.change(subjectBox, { target: { value: savedSubject } });
  await choose(word("field.format"), "HTML");
  fireEvent.change(within(form).getByLabelText(word("field.layout")), { target: { value: "" } });
  pause();
  await waitFor(() => expect(held.has(editsKey(refused.patch))).toBe(true));
  // While its read is held, the saved row's answer stands in for it, dimmed beside the caption for the edits.
  expect(within(factsBand()).getByText(word("preview.unsaved"))).toBeTruthy();
  expect(standsIn()).toBe(true);

  // The later answer first: Save would refuse the edits the form holds.
  act(() => held.get(editsKey(refused.patch))!.release());
  expect(await screen.findByText(word("preview.unsavedRefused"))).toBeTruthy();
  // The earlier answer last renders edits the form no longer holds: it never shows.
  await act(async () => { held.get(editsKey(subject.patch))!.release(); });
  await waitFor(() => expect(busyPane().getAttribute("aria-busy")).toBeNull());
  expect(standsIn()).toBe(false);
  expect(screen.getByText(word("preview.unsavedRefused"))).toBeTruthy();
  expect(screen.queryByText(subject.result.rendered!.subject)).toBeNull();
  expect(screen.queryByTitle(word("preview.frameTitle"))).toBeNull();
});

test("the sample values ride the edits, and edits Save would keep that the sample cannot render say so", async () => {
  const requests: GraphQLWireRequest[] = [];
  const edit = unsavedPreview("sample");
  render(<MessageTemplateStory open={ROW.passwordReset} onRequest={(request) => requests.push(request)} />);
  await screen.findByTitle(word("preview.frameTitle"));
  fireEvent.click(await screen.findByRole("tab", { name: word("form.sample") }));
  pasteInto(await screen.findByRole("textbox", { name: word("field.sample") }), JSON.stringify(edit.patch.sample));
  fireEvent.click(screen.getByRole("tab", { name: word("tab.preview") }));
  // Today the record form's unsaved-changes guard asks before a routed record tab changes the page's search, though
  // the form stays; leaving keeps the edits. This step goes once the record form counts only a new path, or a search
  // key other than the record's tab, as leaving.
  fireEvent.click(await screen.findByRole("button", { name: ui("form.leave") }));
  expect(await screen.findByRole("button", { name: ui("form.discard") })).toBeTruthy();

  await waitFor(() => expect(previewReads(requests).at(-1)).toEqual({ template: ROW.passwordReset, patch: edit.patch }));
  expect(await screen.findByText(word("preview.unsavedUnrendered"))).toBeTruthy();
  const body = screen.getByRole("region", { name: word("preview.refusedRegion") });
  for (const warning of edit.result.warnings) {
    expect(warning.code).toBe("RENDER_ERROR");
    expect(within(body).getByText(warning.detail)).toBeTruthy();
  }
  expect(within(factsBand()).getByText(word("preview.unsaved"))).toBeTruthy();
  expect(screen.queryByText(word("preview.unsavedRefused"))).toBeNull();
  expect(screen.queryByText(word("preview.refused"))).toBeNull();
  expect(screen.queryByTitle(word("preview.frameTitle"))).toBeNull();
});

test("edits Save would refuse are titled so, each refusal announced in Save's words, and one naming a field takes the person there when chosen", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const edit = unsavedPreview("layout");
  render(<MessageTemplateStory open={ROW.passwordReset} />);
  const form = await recordForm();
  await screen.findByTitle(word("preview.frameTitle"));
  await choose(word("field.format"), "HTML");
  const layout = within(form).getByLabelText<HTMLInputElement>(word("field.layout"));
  const source = within(form).getByLabelText<HTMLTextAreaElement>(word("field.source"));
  // Before anything is refused, the reasons' polite region is already in the pane, empty; the band's region
  // holds the saved row's warnings.
  const [toldRegion, reasonsRegion] = politeRegions();
  const saved = RECORDED.previews[ROW.passwordReset]!.warnings.map((warning) => warning.detail);
  expect(reasonsRegion!.textContent).toBe("");
  for (const detail of saved) expect(within(toldRegion!).getByText(detail)).toBeTruthy();
  expect(toldRegion!.parentElement!.className).not.toContain("sr-only");
  fireEvent.change(layout, { target: { value: "" } });
  // The person moves on to Source while the edit settles.
  act(() => source.focus());
  pause();

  expect(await screen.findByText(word("preview.unsavedRefused"))).toBeTruthy();
  const body = screen.getByRole("region", { name: word("preview.unsavedRefusedRegion") });
  expect(edit.result.warnings.map((warning) => [warning.code, warning.field])).toEqual([
    ["SAVE_REFUSED", "layout"], ["SAVE_REFUSED", null],
  ]);
  // The refusals arrive in that same region, announced politely: none is an alert or a status of its own. The band's
  // region is the same node, empty and out of flow.
  expect(reasonsRegion!.isConnected && body.contains(reasonsRegion!)).toBe(true);
  expect(politeRegions()[0]).toBe(toldRegion);
  expect(toldRegion!.textContent).toBe("");
  expect(toldRegion!.parentElement!.className).toContain("sr-only");
  expect(reasonsRegion!.querySelectorAll("[role=alert], [role=status]")).toHaveLength(0);
  for (const warning of edit.result.warnings) {
    const words = within(reasonsRegion!).getByText(warning.detail);
    // A refusal under a column is a control that takes the person to that field; one of the whole row is not.
    expect(words.closest("button") !== null).toBe(warning.field !== null);
  }
  // The one under Layout is titled by the form's label for it, which also describes its control.
  const control = within(body).getByRole("button", { name: edit.result.warnings[0]!.detail });
  expect(describedBy(control)).toBe(word("field.layout"));
  // The refusals arrived while the person was in Source, and left them there.
  expect(document.activeElement).toBe(source);
  fireEvent.click(within(body).getByRole("button", { name: edit.result.warnings[0]!.detail }));
  await waitFor(() => expect(document.activeElement).toBe(within(form).getByLabelText(word("field.layout"))));
  expect(screen.queryByTitle(word("preview.frameTitle"))).toBeNull();
  expect(screen.queryByText(word("preview.refused"))).toBeNull();

  // Discard: the saved row's warnings again in the band's same region, in flow; the reasons' region the same node,
  // empty.
  fireEvent.click(screen.getByRole("button", { name: ui("form.discard") }));
  for (const detail of saved) expect(await within(toldRegion!).findByText(detail)).toBeTruthy();
  const [toldAfter, reasonsAfter] = politeRegions();
  expect(toldAfter).toBe(toldRegion);
  expect(reasonsAfter).toBe(reasonsRegion);
  expect(toldRegion!.parentElement!.className).not.toContain("sr-only");
  expect(reasonsRegion!.textContent).toBe("");
});

test("the same words refused under two fields read apart, each titled by the form's label for its field", async () => {
  const requests: GraphQLWireRequest[] = [];
  const edit = unsavedPreview("variables");
  expect(edit.result.warnings.map((warning) => [warning.detail, warning.field])).toEqual([
    ["Give an object.", "variables"], ["Give an object.", "sample"],
  ]);
  render(<MessageTemplateStory open={ROW.passwordReset} onRequest={(request) => requests.push(request)} />);
  const form = await recordForm();
  await screen.findByTitle(word("preview.frameTitle"));
  // Lists where objects belong: in Sample values, in its own pane, and in Variables, in the sheet.
  fireEvent.click(await screen.findByRole("tab", { name: word("form.sample") }));
  pasteInto(await screen.findByRole("textbox", { name: word("field.sample") }), "[]");
  pasteInto(within(form).getByRole("textbox", { name: word("field.variables") }), "[]");
  fireEvent.click(screen.getByRole("tab", { name: word("tab.preview") }));
  // Today's prompt on a record tab switch, as in the sample test above; leaving keeps the edits.
  fireEvent.click(await screen.findByRole("button", { name: ui("form.leave") }));

  await waitFor(() => expect(previewReads(requests).at(-1)).toEqual({ template: ROW.passwordReset, patch: edit.patch }));
  expect(await screen.findByText(word("preview.unsavedRefused"))).toBeTruthy();
  const [, reasons] = politeRegions();
  // Two refusals, one key each, each titled by its field's label, which also describes the control that goes there.
  const controls = within(reasons!).getAllByRole("button", { name: "Give an object." });
  expect(controls.map(describedBy)).toEqual([word("field.variables"), word("field.sample")]);
  for (const label of [word("field.variables"), word("field.sample")]) {
    expect(within(reasons!).getByText(label)).toBeTruthy();
  }
});

test("the band's polite region is in the pane before the first answer, and that answer's warnings arrive in it", async () => {
  const first = heldAnswer();
  let reads = 0;
  render(<MessageTemplateStory open={ROW.passwordReset}
    beforePreview={() => (reads++ === 0 ? first.promise : undefined)} />);
  await waitFor(() => expect(reads).toBe(1));
  // While the first read is held, the band says what is being previewed, and its region is there, empty.
  const [told] = politeRegions();
  expect(within(factsBand()).getByText(word("preview.saved"))).toBeTruthy();
  expect(told!.textContent).toBe("");
  expect(told!.parentElement!.className).toContain("sr-only");
  act(() => first.release());
  for (const warning of RECORDED.previews[ROW.passwordReset]!.warnings) {
    expect(await within(told!).findByText(warning.detail)).toBeTruthy();
  }
  expect(politeRegions()[0]).toBe(told);
});

test("the warnings' and the reasons' polite regions stay in the pane, taking no room while they hold nothing", async () => {
  render(<MessageTemplateStory open={ROW.conditionalInclude} />);
  await screen.findByTitle(word("preview.frameTitle"));
  const [warnings, reasons] = politeRegions();
  expect([warnings?.textContent, reasons?.textContent]).toEqual(["", ""]);
  // The band is a grid spaced by gaps: its empty region is out of flow, so it takes no row and no gap.
  expect(warnings!.parentElement!.className).toContain("sr-only");
  // The pane's column spaces nothing, and the reasons' region sits in it, after the body.
  expect(busyPane().className).not.toMatch(/(^|\s)gap-/);
  expect(reasons!.parentElement).toBe(busyPane().lastElementChild);
  cleanup();

  // A row that warns shows its warnings in the band's region, in flow, announced politely: none an alert or a status.
  render(<MessageTemplateStory open={ROW.passwordReset} />);
  await screen.findByTitle(word("preview.frameTitle"));
  const [shown] = politeRegions();
  for (const warning of RECORDED.previews[ROW.passwordReset]!.warnings) {
    expect(await within(shown!).findByText(warning.detail)).toBeTruthy();
  }
  expect(shown!.parentElement!.className).not.toContain("sr-only");
  expect(shown!.querySelectorAll("[role=alert], [role=status]")).toHaveLength(0);
});

test("edits the server refuses to preview at all show its refusal, with no frame, and the pane stops being busy", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const denied = RECORDED.unsavedPreviewsLayoutGated[0]!;
  expect(denied.error.extensions.code).toBe("PERMISSION_DENIED");
  render(<MessageTemplateStory open={ROW.passwordReset} layoutGated queryClient={noRetryClient()} />);
  const form = await recordForm();
  await screen.findByTitle(word("preview.frameTitle"));
  await choose(word("field.format"), "HTML");
  fireEvent.change(within(form).getByLabelText(word("field.layout")), { target: { value: "" } });
  pause();

  // The refused read is told in the band's polite region, ahead of anything else there, and is no alert.
  const refusal = await within(factsBand()).findByText(denied.error.message, { exact: false });
  const [told] = politeRegions();
  expect(told!.contains(refusal)).toBe(true);
  expect(told!.querySelectorAll("[role=alert], [role=status]")).toHaveLength(0);
  expect(bandOutline(factsBand(), [])).toEqual(["error", "unsaved"]);
  await waitFor(() => expect(busyPane().getAttribute("aria-busy")).toBeNull());
  expect(screen.queryByTitle(word("preview.frameTitle"))).toBeNull();
  expect(screen.queryByText(word("preview.unsavedRefused"))).toBeNull();
});

test("a member reads a shipped default with its fact alone: no verb, no Preview, no Delete, and the sample as a restriction", async () => {
  render(<MessageTemplateStory open={ROW.welcome} viewer="member" />);
  await screen.findByText(word("notice.shipped"));
  expect(await screen.findByRole("heading", { name: word("form.sample") })).toBeTruthy();
  expect(screen.getAllByText(ui("form.restricted")).length).toBeGreaterThan(0);
  expect(screen.queryByText(word("notice.shippedOverride"), { exact: false })).toBeNull();
  // On a shipped default nobody overrides only the server's answer for this reader (can_override) hides Override.
  expect(screen.queryByRole("button", { name: word("action.override") })).toBeNull();
  expect(screen.queryByRole("button", { name: word("action.keepOverride") })).toBeNull();
  expect(screen.queryByRole("button", { name: ui("list.actions") })).toBeNull();
  expect(screen.queryByRole("tab", { name: word("tab.preview") })).toBeNull();
  expect(screen.queryByText(word("preview.saved"))).toBeNull();
});

/** The Links tab of one message template record, over the same fake and runtime as the stories. */
function BacklinksPane({ id, viewer = "admin", onRequest }: {
  id: string; viewer?: MessageTemplateViewer; onRequest?: (request: GraphQLWireRequest) => void;
}) {
  const schemas = useMemo(() => messageTemplateSchemas({ viewer, onRequest }), [viewer, onRequest]);
  return <RuntimeFixture activeSchema="console" schemas={schemas} runtime={messageTemplateRuntime(viewer)}
    operationDocuments={{ console: operationDocuments }}>
    <RelatedPane context={messageTemplateRecordContext(id)} />
  </RuntimeFixture>;
}

test("Backlinks lists the rows naming a block, each opening its record and marking an overridden shipped default", async () => {
  const expected = RECORDED.backlinks[ROW.button] ?? [];
  render(<BacklinksPane id={ROW.button} />);

  await waitFor(() => expect(screen.queryAllByRole("link").map((link) => link.getAttribute("href")))
    .toEqual(expected.map((row) => `/message-templates/${row.id}`)));
  expect(screen.getByText(word("backlinks.label"))).toBeTruthy();
  expect(screen.getAllByText(word("backlinks.overridden"))).toHaveLength(expected.filter((row) => row.is_overridden).length);
});

test("Backlinks is absent where nothing names the row", async () => {
  const requests: GraphQLWireRequest[] = [];
  render(<BacklinksPane id={ROW.quote} onRequest={(request) => requests.push(request)} />);

  await waitFor(() => expect(requests.some((request) => request.operationName === "MessageTemplateBacklinks")).toBe(true));
  await waitFor(() => expect(screen.queryByText(word("backlinks.label"))).toBeNull());
  expect(screen.queryAllByRole("link")).toEqual([]);
  // With no group showing anything, the Links tab says it is empty.
  expect(await screen.findByText(ui("related.empty.title"))).toBeTruthy();
});

test("Backlinks is no part of a member's Links tab, which reads nothing for it", async () => {
  const requests: GraphQLWireRequest[] = [];
  render(<BacklinksPane id={ROW.button} viewer="member" onRequest={(request) => requests.push(request)} />);

  expect(await screen.findByText(ui("related.empty.title"))).toBeTruthy();
  expect(screen.queryByText(word("backlinks.label"))).toBeNull();
  expect(requests.some((request) => request.operationName === "MessageTemplateBacklinks")).toBe(false);
});
