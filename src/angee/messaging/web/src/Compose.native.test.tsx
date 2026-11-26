// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { enUiMessages as UI } from "@angee/ui/i18n/en";
import { createUiTestProviders } from "@angee/ui/testing";

import { ComposerStory } from "./Compose.stories";
import {
  MESSAGE, RECORDED, recordedSend, type ComposerFixture, type ComposerFixtureCall, type ComposerSeat,
} from "./compose.fixtures.test-support";
import { enMessagingMessages as M } from "./i18n";

// The virtualizer's windowing needs a real layout happy-dom lacks: every row renders.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 96,
    getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * 96, size: 96 })),
    measureElement: () => {},
    scrollToIndex: () => {},
  }),
}));

const { createClient, clearClients } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); });

/** One of the fragment's own words, as its bundle has it. */
function word(key: string, values: Record<string, string | number> = {}): string {
  const text = M[key] ?? M[`${key}_other`];
  if (text === undefined) throw new Error(`The messaging bundle has no ${key}.`);
  return Object.entries(values).reduce((sentence, [name, value]) => sentence.replace(`{${name}}`, String(value)), text);
}

/** One of the shared UI's words, as its bundle has it. */
function ui(key: string): string {
  const text = UI[key];
  if (text === undefined) throw new Error(`The ui bundle has no ${key}.`);
  return text;
}

/** The reasons a recorded in-band refusal gives under one argument (`validation_errors` is JSON on the wire). */
function reasonsUnder(answer: ReturnType<typeof recordedSend>["answer"] | undefined, argument: string): readonly string[] {
  if (!answer || !("data" in answer)) throw new Error("The refusal is not recorded in band.");
  const errors = answer.data.compose_message?.validation_errors as Readonly<Record<string, readonly string[]>> | null | undefined;
  return errors?.[argument] ?? [];
}

function recordedOk(seat: ComposerSeat, text: string): { id: string; message: string } {
  const answer = recordedSend(seat, text).answer;
  if (!("data" in answer) || !answer.data.compose_message?.ok) throw new Error(`The send of "${text}" was not written.`);
  return { id: String(answer.data.compose_message.id), message: String(answer.data.compose_message.message) };
}

function mount(props: Partial<Parameters<typeof ComposerStory>[0]> & { surface: Parameters<typeof ComposerStory>[0]["surface"] }) {
  const calls: ComposerFixtureCall[] = [];
  let fixture: ComposerFixture | undefined;
  const onSent = vi.fn();
  const queryClient = createClient({ defaultOptions: { queries: { retry: false } } });
  const story = (next: typeof props) => <ComposerStory {...next} onSent={onSent} onCall={(call) => calls.push(call)}
    onFixture={(made) => { fixture = made; }} queryClient={queryClient} />;
  const view = render(story(props));
  return {
    calls, onSent,
    /** The same story with other props: another conversation, say. */
    rerender: (next: Partial<typeof props>) => view.rerender(story({ ...props, ...next })),
    sends: () => calls.filter((call) => call.field === "compose_message").map((call) => call.args),
    fixture: () => {
      if (!fixture) throw new Error("The story has not handed over its fixture.");
      return fixture;
    },
  };
}

async function openNewMessage(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: word("compose.newMessage") }));
  return screen.findByRole("dialog", { name: word("compose.newMessage") });
}

/** Type one address into To, Cc or Bcc: a chip each. */
function address(dialog: HTMLElement, field: string, ...entries: readonly string[]): void {
  const input = within(dialog).getByRole("textbox", { name: field });
  for (const entry of entries) fireEvent.change(input, { target: { value: `${entry},` } });
}

function fill(dialog: HTMLElement, values: { subject?: string; text: string }): void {
  if (values.subject) fireEvent.change(within(dialog).getByRole("textbox", { name: word("compose.subject") }), { target: { value: values.subject } });
  fireEvent.change(within(dialog).getByRole("textbox", { name: word("compose.message") }), { target: { value: values.text } });
}

async function send(dialog: HTMLElement): Promise<void> {
  const button = within(dialog).getByRole("button", { name: word("compose.send") });
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  fireEvent.click(button);
}

test("Ana's New message sends on her one channel, the chips as typed, and opens what it wrote", async () => {
  const { sends, onSent } = mount({ surface: "new-message", seat: "ana" });
  const dialog = await openNewMessage();
  // One channel: fixed, labelled, and focus starts in To.
  expect(await within(dialog).findByText("Support desk")).toBeTruthy();
  await waitFor(() => expect(document.activeElement).toBe(within(dialog).getByRole("textbox", { name: word("compose.to") })));
  expect(within(dialog).getByText(word("compose.toHint", { count: RECORDED.seats.ana.recipientLimit }))).toBeTruthy();
  address(dialog, word("compose.to"), "client@example.test");
  fill(dialog, { subject: "Your order", text: "Hello from Support." });
  await send(dialog);
  const written = recordedOk("ana", "Hello from Support.");
  expect(await screen.findByText(written.message)).toBeTruthy();
  await waitFor(() => expect(onSent).toHaveBeenCalledWith(written.id));
  const [sent] = sends();
  expect({ ...sent, idempotency_key: undefined }).toEqual({ ...recordedSend("ana", "Hello from Support.").variables, idempotency_key: undefined });
  expect(sent?.idempotency_key).toEqual(expect.any(String));
  expect(screen.queryByRole("dialog", { name: word("compose.newMessage") })).toBeNull();
});

/** Choose one option of the Channel picker. */
/** The Channel picker: named for its field, then for its field and the channel chosen. */
const CHANNEL_PICKER = new RegExp(`^${word("compose.channel")}`);

async function chooseChannel(dialog: HTMLElement, label: string): Promise<void> {
  fireEvent.click(within(dialog).getByRole("combobox", { name: CHANNEL_PICKER }));
  const option = await screen.findByRole("option", { name: label });
  fireEvent.pointerDown(option, { pointerType: "mouse", button: 0 });
  fireEvent.pointerUp(option, { pointerType: "mouse", button: 0 });
  fireEvent.click(option);
  await waitFor(() => expect(within(dialog).getByRole("combobox", { name: CHANNEL_PICKER }).textContent).toContain(label));
}

/** The dialog's banner: the refusal's reasons, which takes focus. */
async function banner(dialog: HTMLElement): Promise<HTMLElement> {
  let focused: HTMLElement | null = null;
  await waitFor(() => {
    focused = document.activeElement instanceof HTMLElement && document.activeElement.getAttribute("role") === "alert"
      && dialog.contains(document.activeElement) ? document.activeElement : null;
    expect(focused).not.toBeNull();
  });
  return focused!;
}

test("Dana chooses among the channels the server lists as sendable, and Billing is refused at send under Channel", async () => {
  const { calls, sends } = mount({ surface: "new-message", seat: "dana" });
  const dialog = await openNewMessage();
  const channel = await within(dialog).findByRole("combobox", { name: word("compose.channel") });
  await waitFor(() => expect(document.activeElement).toBe(channel));
  fill(dialog, { text: "Your invoice." });
  address(dialog, word("compose.to"), "client@example.test");
  // Send waits for a channel.
  expect(within(dialog).getByRole("button", { name: word("compose.send") }).hasAttribute("disabled")).toBe(true);
  await chooseChannel(dialog, "Billing desk");
  // The picker offers what `sendable` lists: never Archive, which Dana reads and may not send on.
  expect(calls.some((call) => call.field === "channels" && JSON.stringify(call.args.where ?? null).includes("sendable"))).toBe(true);
  await send(dialog);
  const refusal = recordedSend("dana", "Your invoice.").answer;
  if (!("data" in refusal)) throw new Error("The recorded Billing send was answered with an error.");
  const [reason = ""] = reasonsUnder(refusal, "channel");
  expect(reason).toBeTruthy();
  expect(within(await banner(dialog)).getByText(refusal.data.compose_message?.message ?? "")).toBeTruthy();
  expect(within(dialog).getAllByText(reason).length).toBeGreaterThan(0);
  expect(sends()).toHaveLength(1);
});

test.each([
  ["an entry that is no address", ["ana"], "Hello."],
  ["an encoded word", ["=?utf-8?q?Ana?= <ana@example.test>"], "Hello."],
  ["more addresses than the bound", Array.from({ length: 51 }, (_, index) => `guest${index}@example.test`), "Hello, everyone."],
])("a New message to %s is refused under To in the server's words, the values kept", async (_case, to, text) => {
  mount({ surface: "new-message", seat: "ana" });
  const dialog = await openNewMessage();
  address(dialog, word("compose.to"), ...to);
  fill(dialog, { text });
  await send(dialog);
  const answer = RECORDED.sends.find((entry) => entry.seat === "ana" && entry.variables.text === text
    && JSON.stringify(entry.variables.to) === JSON.stringify(to))?.answer;
  const [reason = ""] = reasonsUnder(answer, "to");
  await banner(dialog);
  expect(within(dialog).getAllByText(reason).length).toBeGreaterThan(0);
  expect((within(dialog).getByRole("textbox", { name: word("compose.message") }) as HTMLTextAreaElement).value).toBe(text);
});

test("Send later sends the time with its offset, held, and the toast says it was scheduled", async () => {
  const { sends } = mount({ surface: "new-message", seat: "ana" });
  const dialog = await openNewMessage();
  address(dialog, word("compose.to"), "client@example.test");
  fill(dialog, { text: "See you tomorrow." });
  expect(within(dialog).getByText(word("compose.sendLaterHint"))).toBeTruthy();
  // A day next month, at 09:30 on the viewer's clock.
  fireEvent.click(within(dialog).getByRole("button", { name: word("compose.sendLater") }));
  fireEvent.click(await screen.findByRole("button", { name: "Go to the Next Month" }));
  fireEvent.click((await screen.findAllByRole("button", { name: /15th/ }))[0]!);
  fireEvent.change(await screen.findByLabelText("Time"), { target: { value: "09:30" } });
  await send(dialog);
  expect((await screen.findAllByText(recordedOk("ana", "See you tomorrow.").message)).length).toBeGreaterThan(0);
  const [sent] = sends();
  expect(sent?.queued).toBe(false);
  // The picked wall time in the viewer's zone, with its offset: the server never reads it in its own zone.
  expect(String(sent?.scheduled_at)).toMatch(/^\d{4}-\d{2}-\d{2}T09:30:00(?:Z|[+-]\d{2}:\d{2})$/);
});

test("a lost answer is said in the dialog, and Send again carries the same key and lands on the original", async () => {
  const { sends, onSent } = mount({ surface: "new-message", seat: "ana", lose: true });
  const dialog = await openNewMessage();
  address(dialog, word("compose.to"), "client@example.test");
  fill(dialog, { subject: "Your order", text: "Hello from Support." });
  await send(dialog);
  expect(within(await banner(dialog)).getByText(ui("form.answerLostResend"))).toBeTruthy();
  expect(onSent).not.toHaveBeenCalled();
  await send(dialog);
  const written = recordedOk("ana", "Hello from Support.");
  await waitFor(() => expect(onSent).toHaveBeenCalledWith(written.id));
  const [first, second] = sends();
  expect(second?.idempotency_key).toBe(first?.idempotency_key);
});

test("closed and opened again after a lost answer, the dialog's draft keeps its key", async () => {
  const { sends } = mount({ surface: "new-message", seat: "ana", lose: true });
  let dialog = await openNewMessage();
  address(dialog, word("compose.to"), "client@example.test");
  fill(dialog, { subject: "Your order", text: "Hello from Support." });
  await send(dialog);
  await banner(dialog);
  fireEvent.keyDown(dialog, { key: "Escape" });
  // It held typed values: the shared question first.
  fireEvent.click(await screen.findByRole("button", { name: ui("form.leave") }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: word("compose.newMessage") })).toBeNull());
  dialog = await openNewMessage();
  address(dialog, word("compose.to"), "client@example.test");
  fill(dialog, { subject: "Your order", text: "Hello from Support." });
  await send(dialog);
  expect(await screen.findByText(recordedOk("ana", "Hello from Support.").message)).toBeTruthy();
  const [first, second] = sends();
  expect(second?.idempotency_key).toBe(first?.idempotency_key);
});

test("reopened after a lost and dismissed mail, New message says an earlier attempt may already have been sent, and another mail is told the truth", async () => {
  const { sends } = mount({ surface: "new-message", seat: "ana", lose: true });
  let dialog = await openNewMessage();
  address(dialog, word("compose.to"), "client@example.test");
  fill(dialog, { subject: "Your order", text: "Hello from Support." });
  await send(dialog);
  await banner(dialog);
  fireEvent.keyDown(dialog, { key: "Escape" });
  fireEvent.click(await screen.findByRole("button", { name: ui("form.leave") }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: word("compose.newMessage") })).toBeNull());
  dialog = await openNewMessage();
  expect(within(dialog).getByText(word("compose.mayAlreadyBeSent")).closest("[role=status]")).toBeTruthy();
  address(dialog, word("compose.to"), "ana");
  fill(dialog, { text: "Hello." });
  await send(dialog);
  // Another mail under the kept key: the conflict, in words true for it; the note gives way to them.
  expect(await within(dialog).findByText(word("compose.sentEarlier"))).toBeTruthy();
  expect(within(dialog).queryByText(word("compose.mayAlreadyBeSent"))).toBeNull();
  const [first, second] = sends();
  expect(second?.idempotency_key).toBe(first?.idempotency_key);
});

test("an edited send after a lost one is told the earlier version was sent, and the next send is a new draft", async () => {
  const { sends } = mount({ surface: "new-message", seat: "ana", lose: true });
  const dialog = await openNewMessage();
  address(dialog, word("compose.to"), "client@example.test");
  fill(dialog, { subject: "Your order", text: "Hello from Support." });
  await send(dialog);
  await banner(dialog);
  fill(dialog, { subject: "Your order", text: "Hello from Support, again." });
  await send(dialog);
  expect(await within(dialog).findByText(word("compose.sentEarlier"))).toBeTruthy();
  await banner(dialog);
  await send(dialog);
  await waitFor(() => expect(sends()).toHaveLength(3));
  const [first, second, third] = sends();
  expect(second?.idempotency_key).toBe(first?.idempotency_key);
  expect(third?.idempotency_key).not.toBe(first?.idempotency_key);
});

test("a lost Send now, then Send later: the same key lands on the original, unscheduled, and the toast says what it became", async () => {
  const { sends, onSent } = mount({ surface: "new-message", seat: "ana", lose: true });
  const dialog = await openNewMessage();
  address(dialog, word("compose.to"), "client@example.test");
  fill(dialog, { subject: "Your order", text: "Hello from Support." });
  await send(dialog);
  await banner(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: word("compose.sendLater") }));
  fireEvent.click(await screen.findByRole("button", { name: "Go to the Next Month" }));
  fireEvent.click((await screen.findAllByRole("button", { name: /15th/ }))[0]!);
  fireEvent.change(await screen.findByLabelText("Time"), { target: { value: "09:30" } });
  await send(dialog);
  const written = recordedOk("ana", "Hello from Support.");
  await waitFor(() => expect(onSent).toHaveBeenCalledWith(written.id));
  expect((await screen.findAllByText(written.message)).length).toBeGreaterThan(0);
  const [first, second] = sends();
  expect(second?.queued).toBe(false);
  expect(second?.idempotency_key).toBe(first?.idempotency_key);
});

test("a denial after a lost answer says, in the dialog too, that an earlier attempt may already have been sent", async () => {
  const { fixture } = mount({ surface: "new-message", seat: "ana", lose: true });
  const dialog = await openNewMessage();
  address(dialog, word("compose.to"), "client@example.test");
  fill(dialog, { subject: "Your order", text: "Hello from Support." });
  await send(dialog);
  await banner(dialog);
  fixture().as("anaRevoked");
  await send(dialog);
  const denied = recordedSend("anaRevoked", "Hello from Support.").answer;
  if (!("error" in denied)) throw new Error("The revoked send was recorded as an answer.");
  expect(await within(dialog).findByText(denied.error.message)).toBeTruthy();
  expect(within(dialog).getByText(word("compose.mayAlreadyBeSent")).closest("[role=status]")).toBeTruthy();
});

// --- The reply box ---------------------------------------------------------------------------

function transcriptList(): HTMLElement {
  return screen.getByRole("list", { name: word("transcript.label") });
}

/** The row of one recorded message, found by its text. */
async function row(text: string): Promise<HTMLElement> {
  const found = (await screen.findAllByText(text)).map((node) => node.closest("li.group")).find(Boolean);
  if (!(found instanceof HTMLElement)) throw new Error(`No transcript row reads "${text}".`);
  return found;
}

function replyButtons(): HTMLElement[] {
  return screen.queryAllByRole("button", { name: /^Reply to / });
}

async function openReply(text: string): Promise<HTMLElement> {
  const target = await row(text);
  fireEvent.click(await within(target).findByRole("button", { name: /^Reply to / }));
  return screen.findByRole("region", { name: /^Reply to / });
}

function typeReply(box: HTMLElement, text: string): HTMLTextAreaElement {
  const input = within(box).getByRole("textbox", { name: M["composer.messageLabel"] }) as HTMLTextAreaElement;
  fireEvent.change(input, { target: { value: text } });
  return input;
}

function submitReply(input: HTMLTextAreaElement): void {
  fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
}

test("Reply answers an email from the box docked under the conversation, sends only what an answer takes, and shows it Queued", async () => {
  const { sends } = mount({ surface: "transcript", seat: "ana" });
  const box = await openReply("Please add me.");
  expect(within(box).getByText(word("compose.sentFrom", { channel: "Support desk" }))).toBeTruthy();
  const input = typeReply(box, "Thanks, adding you now.");
  await waitFor(() => expect(document.activeElement).toBe(input));
  // Two presses before the box locks: one request, one toast (the box refuses re-entry, and locks).
  submitReply(input);
  submitReply(input);
  const written = recordedOk("ana", "Thanks, adding you now.");
  expect(await screen.findByText(written.message)).toBeTruthy();
  await waitFor(() => expect(screen.queryByRole("region", { name: /^Reply to / })).toBeNull());
  expect(sends()).toHaveLength(1);
  // The box gave focus back to the conversation.
  await waitFor(() => expect(document.activeElement).toBe(transcriptList()));
  const [sent] = sends();
  // What the server receives: the arguments the document fills.
  expect(Object.keys(sent ?? {}).filter((name) => sent?.[name] != null).sort()).toEqual(["idempotency_key", "parent", "queued", "text"]);
  expect(sent).toMatchObject({ parent: MESSAGE.request, text: "Thanks, adding you now.", queued: true });
  const written_row = await row("Thanks, adding you now.");
  expect(within(written_row).getByText("Queued")).toBeTruthy();
});

test("Reply is offered on the emails of a channel the seat may send on, and nowhere else", async () => {
  mount({ surface: "transcript", seat: "ana", conversation: "sales" });
  expect(await row("A quote, please.")).toBeTruthy();
  await waitFor(() => expect(transcriptList()).toBeTruthy());
  expect(replyButtons()).toEqual([]);
  cleanup();
  mount({ surface: "transcript", seat: "dana", conversation: "sales" });
  expect(await within(await row("A quote, please.")).findByRole("button", { name: /^Reply to / })).toBeTruthy();
});

test("a refused reply is toasted in the server's words and keeps its text", async () => {
  mount({ surface: "transcript", seat: "ana" });
  const box = await openReply("Who can see this?");
  const input = typeReply(box, "Only you.");
  submitReply(input);
  const answer = recordedSend("ana", "Only you.").answer;
  if (!("data" in answer)) throw new Error("The recorded refusal was answered with an error.");
  expect((await screen.findAllByText(answer.data.compose_message?.message ?? "")).length).toBeGreaterThan(0);
  expect(input.value).toBe("Only you.");
  expect(screen.getByRole("region", { name: /^Reply to / })).toBeTruthy();
});

test("a reply's lost answer is told in the box's note, and Send again lands on the original", async () => {
  const { sends } = mount({ surface: "transcript", seat: "ana", lose: true });
  const box = await openReply("Please add me.");
  const input = typeReply(box, "Thanks, adding you now.");
  submitReply(input);
  expect(await within(box).findByRole("status")).toHaveProperty("textContent", ui("form.answerLostResend"));
  submitReply(input);
  expect(await screen.findByText(recordedOk("ana", "Thanks, adding you now.").message)).toBeTruthy();
  const [first, second] = sends();
  expect(second?.idempotency_key).toBe(first?.idempotency_key);
});

test("a replay refused for a revoked right says an earlier attempt may already have been sent", async () => {
  mount({ surface: "transcript", seat: "ana", lose: true, revokeAfterSend: true });
  const box = await openReply("Please add me.");
  const input = typeReply(box, "Thanks, adding you now.");
  submitReply(input);
  await within(box).findByText(ui("form.answerLostResend"));
  submitReply(input);
  const denied = recordedSend("anaRevoked", "Thanks, adding you now.").answer;
  if (!("error" in denied)) throw new Error("The revoked replay was recorded as an answer.");
  expect((await screen.findAllByText(denied.error.message)).length).toBeGreaterThan(0);
  expect(await within(box).findByText(word("compose.replyMayAlreadyBeSent"))).toBeTruthy();
});

test("Reply on another email retargets the box and keeps its text; Cancel asks first and returns to the row", async () => {
  mount({ surface: "transcript", seat: "ana" });
  let box = await openReply("Please add me.");
  typeReply(box, "A draft I keep.");
  box = await openReply("Who can see this?");
  expect((within(box).getByRole("textbox", { name: M["composer.messageLabel"] }) as HTMLTextAreaElement).value).toBe("A draft I keep.");
  expect(within(box).getByText("Who can see this?")).toBeTruthy();
  fireEvent.click(within(box).getByRole("button", { name: M["composer.cancelReply"] }));
  fireEvent.click(await screen.findByRole("button", { name: ui("form.stay") }));
  expect(screen.getByRole("region", { name: /^Reply to / })).toBeTruthy();
  fireEvent.click(within(box).getByRole("button", { name: M["composer.cancelReply"] }));
  fireEvent.click(await screen.findByRole("button", { name: ui("form.leave") }));
  await waitFor(() => expect(screen.queryByRole("region", { name: /^Reply to / })).toBeNull());
  const target = await row("Who can see this?");
  await waitFor(() => expect(document.activeElement).toBe(within(target).getByRole("button", { name: /^Reply to / })));
});

test("after a lost answer, Reply on another email keeps the conversation's draft: the note stays, the same key goes, and a conflict is told", async () => {
  const { sends } = mount({ surface: "transcript", seat: "ana", lose: true });
  let box = await openReply("Please add me.");
  const input = typeReply(box, "Thanks, adding you now.");
  submitReply(input);
  await within(box).findByText(ui("form.answerLostResend"));
  box = await openReply("Who can see this?");
  expect(within(box).getByRole("status").textContent).toBe(ui("form.answerLostResend"));
  submitReply(within(box).getByRole("textbox", { name: M["composer.messageLabel"] }) as HTMLTextAreaElement);
  // The first reply was written: the same key with another parent is the conflict, told; nothing is written twice.
  expect(await within(box).findByText(word("compose.sentEarlier"))).toBeTruthy();
  const [first, second] = sends();
  expect(second).toMatchObject({ parent: MESSAGE.unaddressed, idempotency_key: first?.idempotency_key });
});

test("a box that went away while sending still reports a lost answer, in the shared late report's words", async () => {
  const { fixture, rerender } = mount({ surface: "transcript", seat: "ana", lose: true });
  const box = await openReply("Please add me.");
  const release = fixture().holdNextAnswer();
  submitReply(typeReply(box, "Thanks, adding you now."));
  // The person moves to another conversation while it sends: the box goes.
  rerender({ conversation: "sales" });
  await waitFor(() => expect(screen.queryByRole("region", { name: /^Reply to / })).toBeNull());
  await act(async () => release());
  expect((await screen.findAllByText(ui("form.answerLost"))).length).toBeGreaterThan(0);
});

test("a request refused before it was sent is left to the connection boundary: no note, no toast", async () => {
  const { fixture } = mount({ surface: "transcript", seat: "ana" });
  const box = await openReply("Please add me.");
  fixture().refuseNextAsUnsent();
  const input = typeReply(box, "Thanks, adding you now.");
  submitReply(input);
  await waitFor(() => expect(input.readOnly).toBe(false));
  expect(within(box).getByRole("status").textContent).toBe("");
  expect(document.querySelector('[data-type="danger"]')).toBeNull();
  expect(input.value).toBe("Thanks, adding you now.");
});

test("each message written here and going out shows its delivery; synced and received mail show none", async () => {
  mount({ surface: "transcript", seat: "cleo", noReply: true });
  const expected: Record<string, readonly string[]> = {
    "We are on it.": ["Queued"],
    "Done: you are in.": ["Sent"],
    "Your access is ready.": ["Failed", word("transcript.attempts", { count: 2 }), word("transcript.deliveryDetails")],
    "Checking with the team.": ["Queued", word("transcript.nextTry"), "1 attempt", word("transcript.deliveryDetails")].slice(0, 3),
    "One more thing.": ["Uncertain", "1 attempt", word("transcript.deliveryDetails")],
    "Reminder.": ["Draft", word("transcript.sendsAt")],
  };
  for (const [text, words] of Object.entries(expected)) {
    const target = await row(text);
    for (const expectedWord of words) expect(within(target).getByText(expectedWord, { exact: false })).toBeTruthy();
  }
  // Cleo reads no sender of what went out: each names the channel it went out from.
  expect(within(await row("We are on it.")).getByText("Support desk")).toBeTruthy();
  const failed = within(await row("Your access is ready.")).getByRole("link", { name: word("transcript.deliveryDetails") });
  expect(failed.getAttribute("href")).toContain(MESSAGE.failed);
  for (const text of ["Sent from the desk's own mail client.", "Please add me."]) {
    const quiet = await row(text);
    for (const label of ["Draft", "Queued", "Sent", "Failed", "Uncertain", "Synced"]) expect(within(quiet).queryByText(label)).toBeNull();
  }
  // A host that offers no Reply reads no gate and shows none.
  expect(replyButtons()).toEqual([]);
});

test("a reader who may send nowhere sees neither New message nor Reply", async () => {
  const { calls } = mount({ surface: "transcript", seat: "ben", withNewMessage: true });
  await row("Please add me.");
  await waitFor(() => expect(calls.some((call) => call.field === "message_recipient_limit")).toBe(true));
  await waitFor(() => expect(calls.some((call) => call.field === "channels" && JSON.stringify(call.args.where).includes("_in"))).toBe(true));
  expect(screen.queryByRole("button", { name: word("compose.newMessage") })).toBeNull();
  expect(replyButtons()).toEqual([]);
});

test("under a preview nothing sends: the box is read-only and the dialog's Send is off", async () => {
  const { sends } = mount({ surface: "transcript", seat: "ana", preview: true, withNewMessage: true });
  const box = await openReply("Please add me.");
  expect(within(box).getByText(ui("viewAs.readOnly"))).toBeTruthy();
  const input = within(box).getByRole("textbox", { name: M["composer.messageLabel"] }) as HTMLTextAreaElement;
  expect(input.readOnly).toBe(true);
  submitReply(input);
  const dialog = await openNewMessage();
  address(dialog, word("compose.to"), "client@example.test");
  fill(dialog, { text: "Hello from Support." });
  expect(within(dialog).getByRole("button", { name: word("compose.send") }).hasAttribute("disabled")).toBe(true);
  await act(async () => undefined);
  expect(sends()).toEqual([]);
});

// --- The message page --------------------------------------------------------------------------

test.each([
  ["Cleo, who manages Support, on the failed message", "cleo", "failed", { reason: true, verbs: [M["messages.retryDelivery"]] }],
  ["Cleo on the uncertain message", "cleo", "uncertain", { reason: true, verbs: [M["messages.retryDelivery"], M["messages.markAsSent"]] }],
  ["Ana, who wrote on Support, on the failed message", "ana", "failed", { reason: false, verbs: [] }],
] as const)("the message page reads why delivery failed and offers its verbs only to %s who may act", async (_case, seat, state, { reason, verbs }) => {
  mount({ surface: "message-page", seat, message: MESSAGE[state] });
  const page = RECORDED.messagePage[seat][state];
  if (!("data" in page) || !page.data.messages_by_pk) throw new Error("The message page row is not recorded.");
  const row = page.data.messages_by_pk;
  fireEvent.click(await screen.findByRole("tab", { name: word("messages.groupEnvelope") }));
  expect(await screen.findByText(word("messages.deliveryAttempts"))).toBeTruthy();
  expect(screen.getAllByText(String(row.delivery_attempts)).length).toBeGreaterThan(0);
  if (reason) expect(screen.getAllByText(String(row.delivery_error)).length).toBeGreaterThan(0);
  else expect(screen.queryByText(word("messages.deliveryError"))).toBeNull();
  for (const verb of [M["messages.retryDelivery"], M["messages.markAsSent"]]) {
    expect(screen.queryAllByRole("button", { name: verb }).length > 0).toBe((verbs as readonly (string | undefined)[]).includes(verb));
  }
});
