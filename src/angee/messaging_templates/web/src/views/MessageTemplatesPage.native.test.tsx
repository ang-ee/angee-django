// @vitest-environment happy-dom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useMemo } from "react";
import { expect, test } from "vitest";
import { operationDocuments } from "@angee/gql/console/actions";
import { RuntimeFixture, type GraphQLWireRequest } from "@angee/storybook/testing";
import { RelatedPane } from "@angee/ui";

import {
  KEEP_REFUSAL, MESSAGE_TEMPLATE_RESOURCE, RECORDED, ROW, messageTemplateRecordContext, messageTemplateRuntime,
  messageTemplateSchemas, type MessageTemplateFixtureCall, type MessageTemplateViewer,
} from "../fixtures.test-support";
import { MessageTemplateStory } from "./MessageTemplatesPage.stories";

const NOTICE = {
  shipped: "This is a shipped default.",
  changed: "The shipped default changed since this override started or was last kept.",
  keepHint: "Keep override keeps this text.",
  live: "Saved changes go live for every message at once.",
} as const;

/** The sheet's section headings and field labels, in the order the form shows them; panes are outside the form. */
async function sheetOutline(firstSection = "Message template"): Promise<string[]> {
  const form = (await screen.findByRole("heading", { name: firstSection })).closest("form");
  if (!form) throw new Error("The message template form renders no form element.");
  return [...form.querySelectorAll("h2, h3, label")].map((node) => node.textContent?.trim() ?? "").filter(Boolean);
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
  await choose("Kind", kindLabel);
  fireEvent.change(screen.getByLabelText("Key"), { target: { value: key } });
  fireEvent.change(screen.getByLabelText("Language"), { target: { value: language } });
}

/** The calls the fake answered for one root field, by their arguments. */
function argsOf(calls: readonly MessageTemplateFixtureCall[], field: string): readonly Readonly<Record<string, unknown>>[] {
  return calls.filter((call) => call.field === field).map((call) => call.args);
}

const TEMPLATE_OUTLINE = [
  "Message template", "Kind", "Key", "Language",
  "Message", "Subject", "Preheader", "Layout",
  "Content", "Format", "Source", "Plain-text source",
  "Template variables", "Variables",
  "Details", "Description",
];
const MESSAGE_SECTION = ["Message", "Subject", "Preheader", "Layout"];

test.each([
  ["a template override", ROW.passwordReset, TEMPLATE_OUTLINE],
  ["a block", ROW.notice, TEMPLATE_OUTLINE.filter((label) => !MESSAGE_SECTION.includes(label))],
])("%s shows its identity first, then each section in its place, and no empty section", async (_case, open, outline) => {
  render(<MessageTemplateStory open={open} />);
  await screen.findByLabelText("Description");
  expect(await sheetOutline()).toEqual(outline);
});

test("New shows only the name and the identity, sends those four with a replay key, and opens the seeded row with its identity fixed", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  render(<MessageTemplateStory open="new" onCall={(call) => calls.push(call)} />);
  expect(await sheetOutline()).toEqual(["Message template", "Kind", "Key", "Language"]);
  expect(screen.queryByRole("tab", { name: "Preview" })).toBeNull();
  expect(screen.queryByText("Sample values")).toBeNull();
  const recorded = RECORDED.insert.find((entry) => "changes" in entry && entry.object.kind === "template");
  if (!recorded || !("changes" in recorded)) throw new Error("The recorded fixtures have no new template.");
  await fillNew(recorded.object);
  fireEvent.click(screen.getByRole("button", { name: "Create" }));

  await waitFor(() => expect(argsOf(calls, "insert_message_templates_one")).toHaveLength(1));
  const [insert] = argsOf(calls, "insert_message_templates_one");
  expect(insert?.object).toEqual(recorded.object);
  expect(typeof insert?.idempotency_key).toBe("string");
  expect(String(insert?.idempotency_key)).not.toBe("");
  const seeded = recorded.changes.rows[0]!;
  expect(await screen.findByDisplayValue(String(seeded.source))).toBeTruthy();
  expect(await screen.findByDisplayValue(String(seeded.subject))).toBeTruthy();
  expect(argsOf(calls, "message_templates_by_pk").map((args) => args.id)).toContain(seeded.id);
  expect(screen.queryByRole("textbox", { name: "Key" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Language" })).toBeNull();
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
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await screen.findByText("Save failed");
  expect(await screen.findByText(message)).toBeTruthy();
  expect(screen.getByRole("textbox", { name: "Name" })).toBeTruthy();
});

test("New shows a key the server refuses under the key", async () => {
  const refused = RECORDED.insert.find((entry) => "error" in entry && entry.error.extensions.validationErrors?.key);
  if (!refused || !("error" in refused)) throw new Error("The recorded fixtures have no refused key.");
  render(<MessageTemplateStory open="new" />);
  await fillNew(refused.object);
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  const key = screen.getByLabelText("Key");
  await waitFor(() => expect(key.getAttribute("aria-invalid")).toBe("true"));
  const described = (key.getAttribute("aria-describedby") ?? "").split(" ")
    .map((id) => document.getElementById(id)?.textContent ?? "");
  expect(described.some((text) => text.includes(refused.error.extensions.validationErrors!.key![0]!))).toBe(true);
});

test("Override on a shipped default sends its id and lands on the deployment's override", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  render(<MessageTemplateStory open={ROW.welcome} onCall={(call) => calls.push(call)} />);
  await screen.findByText(NOTICE.shipped);
  expect(screen.queryByRole("button", { name: "Keep override" })).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: "Override" }));

  await waitFor(() => expect(argsOf(calls, "override_message_template")).toEqual([{ template: ROW.welcome }]));
  const result = RECORDED.override[ROW.welcome]!.result;
  expect(await screen.findByText(result.message)).toBeTruthy();
  await waitFor(() => expect(argsOf(calls, "message_templates_by_pk").map((args) => args.id)).toContain(result.id));
  expect(await screen.findByText(NOTICE.live, { exact: false })).toBeTruthy();
  expect(screen.queryByText(NOTICE.shipped, { exact: false })).toBeNull();
  expect(screen.queryByRole("button", { name: "Override" })).toBeNull();
});

test("Keep override sends the override's id, and the default-updated warning goes", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  render(<MessageTemplateStory open={ROW.recordCard} onCall={(call) => calls.push(call)} />);
  await screen.findByText(NOTICE.changed);
  fireEvent.click(await screen.findByRole("button", { name: "Keep override" }));

  await waitFor(() => expect(argsOf(calls, "keep_message_template_override")).toEqual([{ template: ROW.recordCard }]));
  await waitFor(() => expect(screen.queryByText(NOTICE.changed, { exact: false })).toBeNull());
  expect(await screen.findByText(NOTICE.live, { exact: false })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Keep override" })).toBeNull();
});

test("a refused Keep override says why, in the server's words, and the warning stays", async () => {
  render(<MessageTemplateStory open={ROW.recordCard} refuseKeep />);
  await screen.findByText(NOTICE.changed);
  fireEvent.click(await screen.findByRole("button", { name: "Keep override" }));

  expect(await screen.findByText(KEEP_REFUSAL.message, { exact: false })).toBeTruthy();
  expect(screen.getByText(NOTICE.changed)).toBeTruthy();
});

test("Delete on a block another row includes shows the server's refusal", async () => {
  render(<MessageTemplateStory open={ROW.notice} />);
  await screen.findByText(NOTICE.live, { exact: false });
  fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));

  const refusal = RECORDED.delete[ROW.notice]!.preview.refusals[0]!;
  expect(await screen.findByText(refusal)).toBeTruthy();
});

test("a save the server refuses shows each of its reasons in the one Save failed banner, run together", async () => {
  const calls: MessageTemplateFixtureCall[] = [];
  const refusal = RECORDED.save[0]!;
  render(<MessageTemplateStory open={ROW.passwordReset} refuseSave onCall={(call) => calls.push(call)} />);
  await screen.findByLabelText("Layout");
  await choose("Format", "HTML");
  fireEvent.change(screen.getByLabelText("Layout"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await screen.findByText("Save failed");
  expect(await screen.findByText(refusal.error.extensions.formErrors!.join(" "))).toBeTruthy();
  expect(argsOf(calls, "update_message_templates_by_pk").map((args) => args._set)).toEqual([refusal.set]);
});

test("choosing a brand kit sends its id as brand_kit, and the default kit sends none", async () => {
  const requests: GraphQLWireRequest[] = [];
  render(<MessageTemplateStory open={ROW.welcome} onRequest={(request) => requests.push(request)} />);
  await screen.findByTitle("Message preview");
  const previews = () => requests.filter((request) => request.operationName === "MessageTemplatePreview")
    .map((request) => request.variables);
  expect(previews()[0]).toEqual({ template: ROW.welcome });
  const other = RECORDED.kits.find((kit) => !kit.is_default)!;
  await choose("Brand kit", other.name);

  await waitFor(() => expect(previews().at(-1)).toEqual({ template: ROW.welcome, brand_kit: other.id }));
  const inKit = RECORDED.previewsInKit[other.id]![ROW.welcome]!.rendered!.html;
  await waitFor(() => expect((screen.getByTitle("Message preview") as HTMLIFrameElement).getAttribute("srcdoc"))
    .toContain(inKit));
});

test("the preview's HTML is only inside the sandboxed frame: no scripts, no same origin, never a node of the page", async () => {
  render(<MessageTemplateStory open={ROW.welcome} />);
  const frame = await screen.findByTitle("Message preview") as HTMLIFrameElement;
  const sandbox = (frame.getAttribute("sandbox") ?? "").split(/\s+/);
  expect(sandbox).not.toContain("allow-scripts");
  expect(sandbox).not.toContain("allow-same-origin");
  const rendered = RECORDED.previews[ROW.welcome]!.rendered!;
  // The layout's article wrapper and the rendered sentence are the frame's alone.
  const marker = 'aria-roledescription="email"';
  expect(rendered.html).toContain(marker);
  await waitFor(() => expect(frame.getAttribute("srcdoc")).toContain(rendered.html));
  expect(document.querySelector("[aria-roledescription=email]")).toBeNull();
  expect(rendered.html).toContain("Your Angee account is ready");
  expect(document.body.textContent).not.toContain("Your Angee account is ready");
});

test("the Arabic template's frame document reads right to left", async () => {
  render(<MessageTemplateStory open={ROW.welcomeArabic} />);
  const frame = await screen.findByTitle("Message preview");
  await waitFor(() => expect(frame.getAttribute("srcdoc")).toContain('dir="rtl"'));
  expect(frame.getAttribute("srcdoc")).toContain('<html lang="ar"');
});

test("a template whose preview warns lists each warning in the server's words", async () => {
  render(<MessageTemplateStory open={ROW.passwordReset} />);
  await screen.findByTitle("Message preview");
  for (const warning of RECORDED.previews[ROW.passwordReset]!.warnings) {
    expect(await screen.findByText(warning.detail)).toBeTruthy();
  }
});

test("a template that does not render shows why, in the server's words, and no frame", async () => {
  render(<MessageTemplateStory open={ROW.invoiceSent} />);
  await screen.findByText("This message template does not render");
  for (const warning of RECORDED.previews[ROW.invoiceSent]!.warnings) {
    expect(screen.getByText(warning.detail)).toBeTruthy();
  }
  expect(screen.queryByTitle("Message preview")).toBeNull();
});

test("after a save, the preview reads the row again", async () => {
  const requests: GraphQLWireRequest[] = [];
  render(<MessageTemplateStory open={ROW.quote} onRequest={(request) => requests.push(request)} />);
  await screen.findByTitle("Message preview");
  const reads = () => requests.filter((request) => request.operationName === "MessageTemplatePreview").length;
  const before = reads();
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Someone's words, set apart." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await screen.findByText("Changes saved");
  await waitFor(() => expect(reads()).toBeGreaterThan(before));
});

test("a member reads a row with its facts alone: no verb, no Preview, no Delete, and the sample as a restriction", async () => {
  render(<MessageTemplateStory open={ROW.invoiceSent} viewer="member" />);
  await screen.findByText(NOTICE.changed);
  expect(await screen.findByRole("heading", { name: "Sample values" })).toBeTruthy();
  expect(screen.getAllByText("Restricted").length).toBeGreaterThan(0);
  expect(screen.queryByText(NOTICE.keepHint, { exact: false })).toBeNull();
  expect(screen.queryByRole("button", { name: "Keep override" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Override" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Preview" })).toBeNull();
  expect(screen.queryByText("Preview of the saved message template")).toBeNull();
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

test("Backlinks lists the rows naming a block, each opening its record and marking what does not render", async () => {
  const expected = RECORDED.backlinks[ROW.button] ?? [];
  render(<BacklinksPane id={ROW.button} />);

  await waitFor(() => expect(screen.queryAllByRole("link").map((link) => link.getAttribute("href")))
    .toEqual(expected.map((row) => `/message-templates/${row.id}`)));
  expect(screen.getByText("Backlinks")).toBeTruthy();
  expect(screen.getAllByText("Overridden")).toHaveLength(expected.filter((row) => !row.is_effective).length);
});

test.each<[string, string, MessageTemplateViewer]>([
  ["a block nothing includes", ROW.quote, "admin"],
  ["a member", ROW.button, "member"],
])("Backlinks is absent for %s", async (_case, id, viewer) => {
  const requests: GraphQLWireRequest[] = [];
  render(<BacklinksPane id={id} viewer={viewer} onRequest={(request) => requests.push(request)} />);

  await waitFor(() => expect(requests.some((request) => request.operationName === "MessageTemplateBacklinks")).toBe(true));
  await waitFor(() => expect(screen.queryByText("Backlinks")).toBeNull());
  expect(screen.queryAllByRole("link")).toEqual([]);
});
