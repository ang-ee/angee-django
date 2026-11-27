// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider, formatDateStorage } from "@angee/ui";
import { recordFocusCalls, type RecordedFocusCall } from "@angee/ui/testing";
import type { ChatterViewContext } from "@angee/ui/runtime";

const mocks = vi.hoisted(() => ({
  data: undefined as unknown,
  calls: [] as Array<{ op: string; vars: Record<string, unknown> }>,
  /** The reasons the next schedule or log is refused for, under the verb's keys; none answers ok. */
  refusal: null as Record<string, string[]> | null,
  /** While set, a schedule or log that went out is not answered until it resolves. */
  gate: null as Promise<void> | null,
  useAuthoredQuery: vi.fn(),
}));

function operationName(document: unknown): string {
  const definitions = (document as { definitions?: Array<{ name?: { value?: string } }> }).definitions;
  return definitions?.[0]?.name?.value ?? "";
}

/** The action field each pane operation answers through. */
const RESULT_FIELDS: Record<string, string> = {
  ProjectsScheduleRecordTask: "schedule_record_task",
  ProjectsLogRecordTask: "log_record_task",
};

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useActionResultRun: () => async (fire: () => Promise<unknown>) => fire(),
    // The shared task verbs: (id, arguments) in, the action outcome out.
    useActionOutcomeMutation: (field: string) => [
      vi.fn(async (id: string, args: Record<string, unknown> = {}) => {
        mocks.calls.push({ op: field, vars: { id, ...args } });
        return { ok: true, message: "Done." };
      }),
      { fetching: false, error: null },
    ],
  };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: mocks.useAuthoredQuery,
  useAuthoredMutation: (document: unknown) => {
    const op = operationName(document);
    const mutate = vi.fn(async (vars: Record<string, unknown>) => {
      mocks.calls.push({ op, vars });
      if (mocks.gate) await mocks.gate;
      const reasons = mocks.refusal;
      // As the server answers a refusal (`ActionResult.from_error`): its message joins every reason.
      const answer = reasons
        ? { ok: false, message: Object.values(reasons).flat().join("; "), validation_errors: reasons, id: null }
        : { ok: true, message: "Done.", validation_errors: null, id: "tsk_1" };
      return { [RESULT_FIELDS[op] ?? op]: answer };
    });
    return [mutate, { fetching: false, error: null, reset: vi.fn() }];
  },
}));

import { RecordTasksPane } from "./RecordTasksPane";

function context(sqid = "inv_1"): ChatterViewContext {
  return { route: { modelLabel: "billing.Invoice" }, view: { kind: "record", sqid } } as unknown as ChatterViewContext;
}

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: "tsk_1", title: "Chase the credit note", note: "", status: "OPEN", due_date: "2026-03-05", done_at: null,
    activity_type: "call", assignee: { id: "usr_1", username: "ada", display_name: "Ada" },
    task_actions: ["complete", "drop", "reopen"], ...overrides,
  };
}

function payload(tasks: unknown[]) {
  return {
    record_tasks: tasks,
    activity_types: [
      { id: "act_1", key: "todo", name: "To do", glyph: "circle-check" },
      { id: "act_2", key: "call", name: "Call", glyph: "phone" },
    ],
  };
}

/** A catalog that lists no kind to choose. */
const NO_KINDS = { record_tasks: [], activity_types: [] };

function paneTree() {
  return <ToastProvider><ModalsHost><RecordTasksPane context={context()} /></ModalsHost></ToastProvider>;
}

function renderPane() {
  return render(paneTree());
}

let recorder: ReturnType<typeof recordFocusCalls> | undefined;

beforeEach(() => {
  mocks.data = payload([]);
  mocks.calls = [];
  mocks.refusal = null;
  mocks.gate = null;
  mocks.useAuthoredQuery.mockImplementation(() => ({ data: mocks.data, isFetching: false, error: null }));
});

afterEach(() => {
  recorder?.restore();
  recorder = undefined;
  cleanup();
  vi.clearAllMocks();
});

type Mode = "schedule" | "log";
type Field = "text" | "note" | "activityType" | "day";
/** The pane's fields in the order it draws them. */
const FIELDS: readonly Field[] = ["text", "note", "activityType", "day"];

/** The control that draws each of the pane's fields, found by its name in the mode. */
function control(field: Field, mode: Mode): HTMLElement {
  switch (field) {
    case "text": return screen.getByRole("textbox", { name: mode === "log" ? "What happened" : "What needs doing" });
    case "note": return screen.getByRole("textbox", { name: "Notes" });
    case "activityType": return screen.getByRole("combobox", { name: /^Type/ });
    case "day": return screen.getByRole("button", { name: mode === "log" ? "Day it happened" : "Due date" });
  }
}

/** The form's own submit (the toolbar and the mode switch hold buttons of the same name). */
function submitButton(mode: Mode): HTMLButtonElement {
  return control("text", mode).closest("form")!.querySelector<HTMLButtonElement>("button[type='submit']")!;
}

function occurrences(text: string): number {
  return (document.body.textContent ?? "").split(text).length - 1;
}

/**
 * Drawn invalid for a sighted person. The test DOM loads no stylesheet, so this reads the class the
 * control's danger border comes from: a field control's follows its `data-invalid`, any other
 * control's is its own recipe's `border-danger`.
 */
function drawnInvalid(target: HTMLElement): boolean {
  return target.classList.contains("border-danger")
    || (target.hasAttribute("data-invalid") && target.className.includes("data-[invalid]:border-danger"));
}

/** The field's control is marked and drawn invalid and describes itself by the reason, said at the field and not on a banner. */
function expectTiedReason(field: Field, mode: Mode, reason: string) {
  const target = control(field, mode);
  expect(target.getAttribute("aria-invalid")).toBe("true");
  expect(drawnInvalid(target), `${field} draws no danger border: ${target.className}`).toBe(true);
  const described = (target.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean)
    .map((id) => document.getElementById(id));
  // Several reasons under one field read as one text, each once.
  const error = described.find((node) => node?.textContent?.includes(reason));
  expect(error, `${field} is described by ${JSON.stringify(described.map((node) => node?.textContent))}`).toBeTruthy();
  expect(error!.closest("[role='alert']")).toBeNull();
}

async function switchToLog() {
  // The mode switch's own control: the submit button reads "Schedule" until it switches.
  fireEvent.click(screen.getAllByRole("button", { name: "Log" })[0]!);
  await screen.findByRole("textbox", { name: "What happened" });
}

/** Choose a kind through the Select, and wait for its list to close and give the trigger focus back. */
async function chooseKind(name: string) {
  fireEvent.click(control("activityType", "log"));
  const option = await screen.findByRole("option", { name });
  fireEvent.pointerDown(option, { pointerType: "mouse" });
  fireEvent.click(option);
  await waitFor(() => expect(control("activityType", "log").textContent).toContain(name));
  await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
}

/** Pick today in the date popover, then close it: its focus goes back before anything is recorded. */
async function pickToday(mode: Mode) {
  fireEvent.click(control("day", mode));
  const today = await waitFor(() => {
    const day = document.querySelector<HTMLButtonElement>("[role='gridcell'][data-today] button");
    expect(day).not.toBeNull();
    return day!;
  });
  fireEvent.click(today);
  await waitFor(() => expect(control("day", mode).textContent).not.toContain("No date"));
  fireEvent.click(control("day", mode));
  await waitFor(() => expect(screen.queryByRole("grid")).toBeNull());
}

/** Fill what the mode's own checks ask: a text, and to log, a kind and the day. */
async function fill(mode: Mode) {
  if (mode === "log") await switchToLog();
  fireEvent.change(control("text", mode), { target: { value: mode === "log" ? "Called the supplier" : "Confirm the IBAN" } });
  if (mode === "log") {
    await chooseKind("Call");
    await pickToday("log");
  }
}

/** Press the form's submit as a person does (it holds focus first), recording every focus() call from then on. */
function submit(mode: Mode) {
  const button = submitButton(mode);
  button.focus();
  recorder = recordFocusCalls();
  fireEvent.click(button);
  return recorder.calls;
}

describe("RecordTasksPane", () => {
  test("lists planned and done tasks behind the scope chips with their type, assignee and day", () => {
    mocks.data = payload([
      task({ id: "tsk_done", title: "Sent the reminder", status: "DONE", activity_type: "todo" }),
      task(),
    ]);
    renderPane();
    const planned = screen.getByRole("region", { name: "Planned" });
    expect(planned.textContent).toContain("Chase the credit note");
    expect(planned.textContent).toContain("Call");
    expect(planned.textContent).toContain("Ada");
    expect(planned.textContent).not.toContain("Sent the reminder");
    fireEvent.click(screen.getByRole("button", { name: /^Done/ }));
    expect(screen.getByRole("region", { name: "Done" }).textContent).toContain("Sent the reminder");
  });

  test("schedules the catalog's default kind through the record's mutation and clears the form", async () => {
    renderPane();
    fireEvent.change(screen.getByPlaceholderText("What needs doing"), { target: { value: "  Confirm the IBAN  " } });
    fireEvent.submit(screen.getByPlaceholderText("What needs doing").closest("form")!);
    await waitFor(() => expect(mocks.calls).toHaveLength(1));
    expect(mocks.calls[0]).toEqual({
      op: "ProjectsScheduleRecordTask",
      vars: {
        modelLabel: "billing.Invoice", recordId: "inv_1", title: "Confirm the IBAN", note: "", dueDate: null,
        activityType: null,
      },
    });
    await waitFor(() => expect((screen.getByPlaceholderText("What needs doing") as HTMLInputElement).value).toBe(""));
  });

  test("schedules the kind chosen while the catalog lists it", async () => {
    renderPane();
    await fill("schedule");
    await chooseKind("Call");
    submit("schedule");
    await waitFor(() => expect(mocks.calls).toHaveLength(1));
    expect(mocks.calls[0]!.op).toBe("ProjectsScheduleRecordTask");
    expect(mocks.calls[0]!.vars.activityType).toBe("call");
  });

  test("logs through the log's verb: the text, then the note, as its note, the day as the day it happened, and the kind", async () => {
    renderPane();
    await fill("log");
    fireEvent.change(control("note", "log"), { target: { value: "Left a voicemail." } });
    submit("log");
    await waitFor(() => expect(mocks.calls).toHaveLength(1));
    expect(mocks.calls[0]).toEqual({
      op: "ProjectsLogRecordTask",
      vars: {
        modelLabel: "billing.Invoice", recordId: "inv_1", activityType: "call", occurredOn: formatDateStorage(new Date()),
        note: "Called the supplier\nLeft a voicemail.",
      },
    });
  });

  test("completes a planned task through the generic verb with its completion note", async () => {
    mocks.data = payload([task()]);
    renderPane();
    fireEvent.click(screen.getByRole("button", { name: /Chase the credit note/ }));
    fireEvent.change(screen.getByLabelText("Completion note"), { target: { value: "Supplier confirmed." } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Mark done" }));
    await waitFor(() => expect(mocks.calls).toEqual([
      { op: "complete_task", vars: { id: "tsk_1", feedback: "Supplier confirmed." } },
    ]));
  });

  test("cancels a planned task by dropping it", async () => {
    mocks.data = payload([task()]);
    renderPane();
    fireEvent.click(screen.getByRole("button", { name: "Cancel task" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel task" }));
    await waitFor(() => expect(mocks.calls).toEqual([{ op: "drop_task", vars: { id: "tsk_1", reason: "OBSOLETE" } }]));
  });

  test("offers no hand verbs where the task's own verbs admit none", () => {
    mocks.data = payload([task({ task_actions: [] })]);
    renderPane();
    expect(screen.getByRole("region", { name: "Planned" }).textContent).toContain("Chase the credit note");
    expect(screen.queryByRole("checkbox", { name: "Mark done" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel task" })).toBeNull();
  });

  test("shows the empty state for a record with nothing planned", () => {
    renderPane();
    expect(screen.getByText("Nothing planned")).toBeTruthy();
  });

  test("asks for a record outside a record view", () => {
    render(<ToastProvider><ModalsHost><RecordTasksPane context={{ route: { modelLabel: "billing.Invoice" }, view: { kind: "list" } } as unknown as ChatterViewContext} /></ModalsHost></ToastProvider>);
    expect(screen.getByText("No record selected")).toBeTruthy();
  });
});

// The keys the two verbs refuse under (the manager's and the model's, camel-cased by the action
// guard), each with its own reason.
const REASONS = {
  title: "A record task needs a title.",
  note: "A logged exchange needs a nonempty first line.",
  dueDate: "The day falls in a closed period.",
  activityType: "Choose a known kind.",
  assignee: "A record task needs an assignee.",
} as const;
type Key = keyof typeof REASONS;
type Place = Field | "banner";

/**
 * Where each key's reason shows in each mode: at the field the mode's verb names by that key,
 * else on the banner. Scheduling names the text `title` and the day `dueDate`; logging names the
 * text `note` and refuses the day under `dueDate`, and its `title` is no field's (the verb takes it
 * from the note's first line); no mode shows the assignee.
 */
const PLACES: Record<Mode, Record<Key, Place>> = {
  schedule: { title: "text", note: "note", dueDate: "day", activityType: "activityType", assignee: "banner" },
  log: { title: "banner", note: "text", dueDate: "day", activityType: "activityType", assignee: "banner" },
};

/** One row per key a verb refuses under, per mode, then a field with the assignee, then two fields. */
const REFUSALS: readonly (readonly [string, Mode, readonly Key[]])[] = (["schedule", "log"] as const).flatMap((mode) => {
  const keys: Key[][] = [["title"], ["note"], ["dueDate"], ["activityType"], ["assignee"],
    mode === "log" ? ["note", "assignee"] : ["title", "assignee"],
    mode === "log" ? ["note", "dueDate"] : ["title", "dueDate"]];
  return keys.map((row) => [`${mode}, refused under ${row.join(" and ")}`, mode, row] as const);
});

describe("a refused schedule or log says each reason once, where it belongs, and brings the person to it", () => {
  test.each(REFUSALS)("%s", async (_name, mode, keys) => {
    renderPane();
    await fill(mode);
    mocks.refusal = Object.fromEntries(keys.map((key) => [key, [REASONS[key]]]));
    const calls = submit(mode);
    await waitFor(() => expect(mocks.calls).toHaveLength(1));
    for (const key of keys) await screen.findByText(REASONS[key], { exact: false });

    const places = keys.map((key) => PLACES[mode][key]);
    const shownOn = new Set(places);
    for (const key of keys) {
      const reason = REASONS[key];
      // Said once in the whole pane.
      expect(occurrences(reason)).toBe(1);
      const place = PLACES[mode][key];
      if (place === "banner") {
        // A key no field claims, as the binder words it.
        expect(screen.getByRole("alert").textContent).toContain(`${key}: ${reason}`);
      } else {
        // Base UI ties a field control's reason in an effect of its own: wait for it.
        await waitFor(() => expectTiedReason(place, mode, reason));
        // A claimed key never reads raw.
        expect(document.body.textContent).not.toContain(`${key}: `);
      }
    }
    // The fields not refused are not marked, and the banner shows only when a reason has no field.
    for (const field of FIELDS) {
      if (shownOn.has(field)) continue;
      expect(control(field, mode).getAttribute("aria-invalid")).not.toBe("true");
      expect(drawnInvalid(control(field, mode))).toBe(false);
    }
    if (!shownOn.has("banner")) expect(screen.queryByRole("alert")).toBeNull();

    // Focus: the banner when a reason has no field, else the first refused field in the form's order.
    await expectFocusOnly(calls, shownOn.has("banner") ? screen.getByRole("alert") : control(FIELDS.find((field) => shownOn.has(field))!, mode));
  });
});

/** Focus lands on `expected` and stays past the banner's timer; it was asked once, of that target, which the test DOM shows can take it. */
async function expectFocusOnly(calls: readonly RecordedFocusCall[], expected: HTMLElement) {
  await waitFor(() => expect(document.activeElement).toBe(expected));
  // The banner's turn is the timer after the field's: one more timer, and focus has stayed.
  await act(() => new Promise<void>((resolve) => { setTimeout(resolve); }));
  expect(document.activeElement).toBe(expected);
  expect(calls.map((call) => ({ target: call.target.outerHTML.slice(0, 90), takesFocus: call.takesFocus })))
    .toEqual([{ target: expected.outerHTML.slice(0, 90), takesFocus: true }]);
  expect(calls[0]?.target).toBe(expected);
}

describe("a refused field's reasons, all of them, and a kind the pane does not draw", () => {
  test("schedule refused under title with two reasons: both at the text, once each", async () => {
    renderPane();
    await fill("schedule");
    const second = "Use a title nobody else uses.";
    mocks.refusal = { title: [REASONS.title, second] };
    const calls = submit("schedule");
    await waitFor(() => expect(mocks.calls).toHaveLength(1));
    for (const reason of [REASONS.title, second]) {
      await waitFor(() => expectTiedReason("text", "schedule", reason));
      expect(occurrences(reason)).toBe(1);
    }
    expect(screen.queryByRole("alert")).toBeNull();
    await expectFocusOnly(calls, control("text", "schedule"));
  });

  test("an empty catalog: no kind is drawn, so a schedule refused under the kind says it on the banner, which takes focus", async () => {
    mocks.data = NO_KINDS;
    renderPane();
    expect(screen.queryByRole("combobox", { name: /^Type/ })).toBeNull();
    await fill("schedule");
    mocks.refusal = { activityType: [REASONS.activityType] };
    const calls = submit("schedule");
    await waitFor(() => expect(mocks.calls).toHaveLength(1));
    const banner = await screen.findByRole("alert");
    // A key no drawn field claims, as the binder words it, once.
    expect(banner.textContent).toContain(`activityType: ${REASONS.activityType}`);
    expect(occurrences(REASONS.activityType)).toBe(1);
    await expectFocusOnly(calls, banner);
  });

  test("an empty catalog: a log with a text and a day goes out, its kind left to the server", async () => {
    mocks.data = NO_KINDS;
    renderPane();
    await switchToLog();
    fireEvent.change(control("text", "log"), { target: { value: "Called the supplier" } });
    await pickToday("log");
    submit("log");
    await waitFor(() => expect(mocks.calls).toHaveLength(1));
    expect(mocks.calls[0]).toEqual({
      op: "ProjectsLogRecordTask",
      vars: {
        modelLabel: "billing.Invoice", recordId: "inv_1", activityType: "", occurredOn: formatDateStorage(new Date()),
        note: "Called the supplier",
      },
    });
    expect(occurrences("This field is required.")).toBe(0);
  });
});

/** Hold the next schedule or log unanswered; the returned function lets its answer through. */
function holdAnswer(): () => void {
  let open!: () => void;
  mocks.gate = new Promise<void>((resolve) => { open = resolve; });
  return () => { open(); mocks.gate = null; };
}

describe("a catalog that changes while the pane is open: the pane claims and sends the kind as it draws it then", () => {
  test("kinds at the submit, none when the answer lands: a reason under the kind is said on the banner, which takes focus", async () => {
    const pane = renderPane();
    await fill("schedule");
    mocks.refusal = { activityType: [REASONS.activityType] };
    const answer = holdAnswer();
    const calls = submit("schedule");
    await waitFor(() => expect(mocks.calls).toHaveLength(1));
    mocks.data = NO_KINDS;
    pane.rerender(paneTree());
    expect(screen.queryByRole("combobox", { name: /^Type/ })).toBeNull();
    answer();
    const banner = await screen.findByRole("alert");
    expect(banner.textContent).toContain(`activityType: ${REASONS.activityType}`);
    expect(occurrences(REASONS.activityType)).toBe(1);
    await expectFocusOnly(calls, banner);
  });

  test("no kind at the submit, kinds when the answer lands: a reason under the kind is said at its select, which takes focus", async () => {
    mocks.data = NO_KINDS;
    const pane = renderPane();
    await fill("schedule");
    mocks.refusal = { activityType: [REASONS.activityType] };
    const answer = holdAnswer();
    const calls = submit("schedule");
    await waitFor(() => expect(mocks.calls).toHaveLength(1));
    mocks.data = payload([]);
    pane.rerender(paneTree());
    await waitFor(() => expect(control("activityType", "schedule")).toBeTruthy());
    answer();
    await waitFor(() => expectTiedReason("activityType", "schedule", REASONS.activityType));
    expect(occurrences(REASONS.activityType)).toBe(1);
    expect(screen.queryByRole("alert")).toBeNull();
    await expectFocusOnly(calls, control("activityType", "schedule"));
  });

  test.each<readonly [Mode, string, unknown]>([
    ["schedule", "ProjectsScheduleRecordTask", null],
    ["log", "ProjectsLogRecordTask", ""],
  ])("a kind chosen, then the catalog empties: a %s sends no kind", async (mode, op, sent) => {
    const pane = renderPane();
    await fill(mode);
    if (mode === "schedule") await chooseKind("Call");
    mocks.data = NO_KINDS;
    pane.rerender(paneTree());
    expect(screen.queryByRole("combobox", { name: /^Type/ })).toBeNull();
    submit(mode);
    await waitFor(() => expect(mocks.calls).toHaveLength(1));
    expect(mocks.calls[0]!.op).toBe(op);
    expect(mocks.calls[0]!.vars.activityType).toBe(sent);
  });
});

describe("the pane's own checks show at their fields, as a server reason does", () => {
  test.each<readonly [string, Mode, boolean, readonly Field[]]>([
    ["schedule, with no text", "schedule", false, ["text"]],
    ["log, with a text only: the kind and the day at once", "log", true, ["activityType", "day"]],
    ["log, with nothing: the text, the kind and the day at once", "log", false, ["text", "activityType", "day"]],
  ])("%s", async (_name, mode, withText, refused) => {
    renderPane();
    if (mode === "log") await switchToLog();
    if (withText) fireEvent.change(control("text", mode), { target: { value: "Called the supplier" } });
    const calls = submit(mode);
    await waitFor(() => expect(occurrences("This field is required.")).toBe(refused.length));
    for (const field of refused) await waitFor(() => expectTiedReason(field, mode, "This field is required."));
    for (const field of FIELDS) {
      if (!refused.includes(field)) expect(control(field, mode).getAttribute("aria-invalid")).not.toBe("true");
    }
    // Nothing went out, and nothing shows on a banner.
    expect(mocks.calls).toEqual([]);
    expect(screen.queryByRole("alert")).toBeNull();
    // The form library focuses the first refused field in the form's order, and only it.
    const expected = control(refused[0]!, mode);
    await waitFor(() => expect(document.activeElement).toBe(expected));
    expect(calls.length).toBeGreaterThan(0);
    expect(calls.filter((call) => call.target !== expected || !call.takesFocus).map((call) => call.target.outerHTML.slice(0, 90))).toEqual([]);
  });
});
