// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useMemo } from "react";
import { expect, test } from "vitest";
import { operationDocuments } from "@angee/gql/console/actions";
import { RuntimeFixture, type GraphQLWireRequest } from "@angee/storybook/testing";
import { RelatedPane } from "@angee/ui";
import { enUiMessages as UI } from "@angee/ui/i18n/en";

import {
  KEEP_REFUSAL, MESSAGE_TEMPLATE_RESOURCE, RECORDED, ROW, messageTemplateRecordContext, messageTemplateRuntime,
  messageTemplateSchemas, type MessageTemplateFixtureCall, type MessageTemplateViewer,
} from "../fixtures.test-support";
import { enMessagingTemplatesMessages as M } from "../i18n";
import { MessageTemplateStory } from "./MessageTemplatesPage.stories";

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

test.each(refusedIdentities())("New refuses the identity %s in the Save failed banner, in the server's words", async (
  _key, object, message,
) => {
  render(<MessageTemplateStory open="new" />);
  await fillNew(object);
  fireEvent.click(screen.getByRole("button", { name: ui("form.create") }));
  await screen.findByText(ui("form.saveFailed"));
  expect(await screen.findByText(message)).toBeTruthy();
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

test("Keep override sends the override's id, and the default-updated warning goes", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  render(<MessageTemplateStory open={ROW.recordCard} onCall={(call) => calls.push(call)} />);
  await screen.findByText(word("notice.defaultUpdated"));
  fireEvent.click(await screen.findByRole("button", { name: word("action.keepOverride") }));

  await waitFor(() => expect(argsOf(calls, "keep_message_template_override")).toEqual([{ template: ROW.recordCard }]));
  await waitFor(() => expect(screen.queryByText(word("notice.defaultUpdated"), { exact: false })).toBeNull());
  expect(await screen.findByText(word("notice.live"), { exact: false })).toBeTruthy();
  expect(screen.queryByRole("button", { name: word("action.keepOverride") })).toBeNull();
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

test("a refused Keep override says why, in the server's words for this row, and the warning stays", async () => {
  render(<MessageTemplateStory open={ROW.recordCard} refuseKeep />);
  await screen.findByText(word("notice.defaultUpdated"));
  fireEvent.click(await screen.findByRole("button", { name: word("action.keepOverride") }));

  // The toast is also announced in a live region, so the words are on the page more than once.
  expect((await screen.findAllByText(KEEP_REFUSAL.message, { exact: false })).length).toBeGreaterThan(0);
  // Its reason names this row, which another session deleted since it opened; nothing says it overrides no default.
  const reasons = Object.values(KEEP_REFUSAL.validation_errors ?? {}).flat().map(String);
  expect(reasons.length).toBeGreaterThan(0);
  for (const reason of reasons) {
    expect(reason).toContain(ROW.recordCard);
    expect((await screen.findAllByText(reason, { exact: false })).length).toBeGreaterThan(0);
  }
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
  const refusal = RECORDED.save[0]!;
  render(<MessageTemplateStory open={ROW.passwordReset} refuseSave onCall={(call) => calls.push(call)} />);
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
  expect(argsOf(calls, "update_message_templates_by_pk").map((args) => args._set)).toEqual([refusal.set]);
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

test("a template whose preview warns lists each warning in the server's words, first in the band", async () => {
  render(<MessageTemplateStory open={ROW.passwordReset} />);
  await screen.findByTitle(word("preview.frameTitle"));
  const band = screen.getByRole("region", { name: word("preview.factsRegion") });
  const saved = within(band).getByText(word("preview.saved"));
  const subject = within(band).getByText(word("preview.subject"));
  for (const warning of RECORDED.previews[ROW.passwordReset]!.warnings) {
    const shown = await within(band).findByText(warning.detail);
    // Before the saved-row line and the subject: seen whole before the rest of the band.
    expect(shown.compareDocumentPosition(saved) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(shown.compareDocumentPosition(subject) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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
});

test("Backlinks is no part of a member's Links tab, which reads nothing for it", async () => {
  const requests: GraphQLWireRequest[] = [];
  render(<BacklinksPane id={ROW.button} viewer="member" onRequest={(request) => requests.push(request)} />);

  expect(await screen.findByText(ui("related.empty.title"))).toBeTruthy();
  expect(screen.queryByText(word("backlinks.label"))).toBeNull();
  expect(requests.some((request) => request.operationName === "MessageTemplateBacklinks")).toBe(false);
});
