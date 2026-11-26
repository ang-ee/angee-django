// @vitest-environment happy-dom
import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, describe, expect, test } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders, recordFocusCalls, type RecordedFocusCall } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { ActionFormDialog } from "./ActionFormDialog";
import { FormView } from "./FormView";
import { MutationDialog } from "./MutationDialog";
import { jsonSchemaActionArgs } from "./json-schema";
import { invalidFormSubmit, type ValidationErrors } from "./validation-errors";
import type { DescriptorField } from "./DescriptorFieldList";
import type { ActionArg, ActionDescriptor, FieldDescriptor } from "../page";

// Where focus goes after a refusal, on each surface, for the widgets whose control is locked while a
// dialog submits: disabled (select, combobox), or swapped for its read view (date, time, tags, the
// relation pickers). Each case refuses one field alone and records every focus() call with the
// state of its target at that moment.
//
// happy-dom focuses an input inside a disabled fieldset (the schema dialog's) and a non-editable
// editor (JSON while locked), which a browser refuses; it never focuses a disabled or detached
// element. So each call is checked against the fieldset, `contenteditable` and what else the test
// DOM can show (`recordFocusCalls`): the proof that a browser lands there too is a browser check,
// which these tests do not make. Two editors are left to a browser: JSON, not editable while a
// dialog submits, whose focus happy-dom cannot drive (it dispatches `selectionchange` inside
// CodeMirror's own selection update, which CodeMirror refuses; a browser queues the event), and a
// markdown editor holding text, which hides its source while it previews by an app stylesheet
// class these tests do not load.

const meta = (scalar: string, extra: Record<string, unknown> = {}) => ({
  kind: "scalar", scalar, readable: true, filterable: false, sortable: false, aggregatable: false, groupable: false,
  creatable: true, updatable: true, requiredOnCreate: false, ...extra,
});
const ITEMS = [{ id: "itm_1", name: "Alpha" }, { id: "itm_2", name: "Beta" }];
const ITEM = testDataResource("probe.Item", { fields: [{ name: "id", ...meta("ID") }, { name: "name", ...meta("String") }] as never });
const thing = (field: Record<string, unknown> | null) => testDataResource("probe.Thing", {
  createFields: ["title", "f"],
  fields: [{ name: "id", ...meta("ID") }, { name: "title", ...meta("String") }, ...(field ? [{ name: "f", ...field }] : [])] as never,
});
const { Provider, createClient, clearClients } = createUiTestProviders({ resources: [thing(null), ITEM], apiUrl: "test://focus" });

let recorder: ReturnType<typeof recordFocusCalls> | undefined;
afterEach(() => { recorder?.restore(); recorder = undefined; cleanup(); clearClients(); });
const isBanner = (call: RecordedFocusCall) => call.target.getAttribute("role") === "alert";

const REASON = "This value is refused.";
const OPTIONS = [{ value: "a", label: "Apple" }, { value: "b", label: "Berry" }];
const RELATION = { resource: "probe.Item", labelField: "name" };

function Shell({ children, resources = [thing(null), ITEM] }: { children: React.ReactNode; resources?: readonly ReturnType<typeof testDataResource>[] }) {
  const [state] = React.useState(() => ({
    provider: {
      getOne: async ({ resource, id }: { resource: string; id: unknown }) => ({
        data: resource.toLowerCase().includes("item") ? ITEMS.find((item) => item.id === id) ?? ITEMS[0] : { id: "thing-1", title: "First" },
      }),
      getMany: async ({ ids }: { ids: unknown[] }) => ({ data: ITEMS.filter((item) => ids.includes(item.id)) }),
      getList: async () => ({ data: ITEMS, total: ITEMS.length }),
    },
    client: createClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }),
    router: createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) }),
  }));
  return <Provider resources={resources as never} dataProvider={state.provider as never} queryClient={state.client}>
    <RouterContextProvider router={state.router}><ModalsHost><ToastProvider>
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>{children}</AppRuntimeProvider>
    </ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>;
}

type Surface = "mutation" | "action" | "schema";
interface FieldCase {
  name: string;
  value: unknown;
  field: DescriptorField;
  arg: ActionArg;
  schema: Record<string, unknown>;
  /** The field's own control, the element marked invalid. */
  control: (dialog: HTMLElement) => HTMLElement | null;
}

function scalarCase(name: string, widget: string, value: unknown, extra: { options?: typeof OPTIONS; schema?: Record<string, unknown>; control: FieldCase["control"] }): FieldCase {
  return {
    name, value,
    field: { name: "f", label: "Field", widget, ...(extra.options ? { options: extra.options } : {}) },
    arg: { name: "f", label: "Field", widget, optional: true, defaultValue: value, ...(extra.options ? { options: extra.options } : {}) } as ActionArg,
    schema: extra.schema ?? { type: "string", label: "Field", widget },
    control: extra.control,
  };
}

const textbox = (dialog: HTMLElement) => within(dialog).queryByRole("textbox", { name: "Field" });
const combobox = (dialog: HTMLElement) => within(dialog).queryByRole("combobox", { name: /^Field/ });

const CASES: readonly FieldCase[] = [
  scalarCase("a text field (the control case: a read-only input still takes focus)", "text", "abc", { control: textbox }),
  scalarCase("a select (its trigger is disabled while the dialog submits)", "select", "a",
    { options: OPTIONS, schema: { type: "string", label: "Field", widget: "select", enum: ["a", "b"] }, control: combobox }),
  scalarCase("a combobox (disabled while the dialog submits)", "combobox", "a",
    { options: OPTIONS, schema: { type: "string", label: "Field", widget: "combobox", enum: ["a", "b"] }, control: combobox }),
  scalarCase("a date (its trigger is swapped for the read view while the dialog submits)", "date", "2026-11-20",
    { schema: { type: "string", format: "date", label: "Field", widget: "date" }, control: (dialog) => within(dialog).queryByRole("button", { name: "Field" }) }),
  scalarCase("a time (swapped for the read view)", "time", "10:00",
    { control: (dialog) => dialog.querySelector<HTMLElement>("input[type='time']") }),
  scalarCase("a tag input (swapped for the read view)", "tagInput", ["ana"],
    { schema: { type: "array", items: { type: "string" }, label: "Field", widget: "tagInput" }, control: textbox }),
  {
    name: "a relation (the picker is swapped for the record's reference)", value: "itm_1",
    field: { name: "f", label: "Field", relation: RELATION },
    arg: { name: "f", label: "Field", argKind: "relation", resource: "probe.Item", optional: true, defaultValue: "itm_1" },
    schema: { type: "string", label: "Field", relation: RELATION },
    control: combobox,
  },
  {
    name: "a relation list (swapped for the records' chips)", value: ["itm_1"],
    field: { name: "f", label: "Field", widget: "many2many", relation: RELATION },
    arg: { name: "f", label: "Field", argKind: "relationList", resource: "probe.Item", optional: true, fromContext: () => ["itm_1"] },
    schema: { type: "array", items: { type: "string" }, label: "Field", widget: "many2many", relation: RELATION },
    control: combobox,
  },
];

function refused(issues: Partial<ValidationErrors> = {}) {
  return invalidFormSubmit({ fieldErrors: { f: [REASON] }, formErrors: [], ...issues });
}

function surface(kind: Surface, probe: Pick<FieldCase, "field" | "arg" | "schema" | "value">, answer: ReturnType<typeof invalidFormSubmit>): React.ReactElement {
  if (kind === "mutation") {
    return <MutationDialog open onOpenChange={() => undefined} title="Probe" fields={[probe.field]}
      initialValues={{ f: probe.value }} submitLabel="Send" parseValues={(values) => values} onSubmit={async () => answer} />;
  }
  const args = kind === "action" ? [probe.arg]
    : () => jsonSchemaActionArgs({ type: "object", properties: { f: probe.schema } }, defaultWidgets, { initialValues: { f: probe.value } });
  const action: ActionDescriptor = { id: "probe", label: "Send", submit: async () => answer, args };
  return <ActionFormDialog open onOpenChange={() => undefined} context={{ record: null, selectedIds: [] }} action={action} />;
}

/**
 * Render, wait for the field's control (a lazy editor mounts late), press the real submit (Send
 * holds focus first, as a click leaves it), and return the dialog and the focus calls from then on.
 */
async function refuse(ui: React.ReactElement, control?: FieldCase["control"]) {
  render(<Shell>{ui}</Shell>);
  const dialog = await screen.findByRole("dialog");
  const send = await within(dialog).findByRole("button", { name: "Send" }) as HTMLButtonElement;
  await waitFor(() => expect(send.disabled).toBe(false));
  if (control) await waitFor(() => expect(control(dialog)).not.toBeNull());
  send.focus();
  recorder = recordFocusCalls();
  fireEvent.click(send);
  await within(dialog).findByText(REASON);
  return { dialog, calls: recorder.calls };
}

const SURFACES: readonly [Surface, string][] = [
  ["mutation", "a mutation dialog"],
  ["action", "an action dialog with typed arguments"],
  ["schema", "an action dialog with schema arguments (its fields sit in a fieldset disabled while it submits)"],
];

describe.each(SURFACES)("a field refused alone, in %s", (kind) => {
  test.each(CASES)("$name: focus lands on its control once that control takes focus, and the banner keeps out", async (probe) => {
    const { dialog, calls } = await refuse(surface(kind, probe, refused()), probe.control);
    await waitFor(() => expect(document.activeElement).toBe(probe.control(dialog)));
    expect(document.activeElement?.getAttribute("aria-invalid")).toBe("true");
    const fieldCalls = calls.filter((call) => !isBanner(call));
    expect(fieldCalls.length).toBeGreaterThan(0);
    // Asked while the control was locked, focus is lost to a browser: every call came once it takes focus.
    expect(fieldCalls.filter((call) => !call.takesFocus).map((call) => call.target.outerHTML.slice(0, 80))).toEqual([]);
    expect(calls.filter(isBanner)).toEqual([]);
  });

  test("a refusal with a reason no field shows still brings the person to the banner", async () => {
    const { dialog } = await refuse(surface(kind, CASES[0]!, refused({ formErrors: ["The channel cannot send now."] })));
    const banner = within(dialog).getByText("The channel cannot send now.").closest("[role='alert']");
    await waitFor(() => expect(document.activeElement).toBe(banner));
  });
});

describe("a refused field whose control takes no focus", () => {
  /** A dialog control that hands the form no focus target. */
  const noTarget: DescriptorField = {
    name: "f", label: "Field",
    control: ({ id, value, invalid, onChange }) => <input id={id} aria-label="Field" aria-invalid={invalid || undefined}
      value={String(value ?? "")} onChange={(event) => onChange(event.currentTarget.value)} />,
  };

  test.each([
    ["a mutation dialog", <MutationDialog key="m" open onOpenChange={() => undefined} title="Probe" fields={[noTarget]}
      initialValues={{ f: "abc" }} submitLabel="Send" parseValues={(values) => values} onSubmit={async () => refused()} />],
    ["an action dialog with a field definition", <ActionFormDialog key="a" open onOpenChange={() => undefined} context={{ record: null, selectedIds: [] }}
      action={{ id: "probe", label: "Send", submit: async () => refused(), args: { fields: [noTarget], defaultValues: { f: "abc" } } }} />],
  ] as const)("in %s leaves focus to the banner", async (_name, ui) => {
    const { dialog } = await refuse(ui);
    const banner = within(dialog).getByText(/Please fix the highlighted fields/).closest("[role='alert']");
    await waitFor(() => expect(document.activeElement).toBe(banner));
  });
});

/** A record form showing `f` through `field`, its save refused for `f` alone: Save pressed, and the focus calls from then on. */
async function refuseOnRecord(field: FieldDescriptor, value: unknown, scalar = "String") {
  render(<Shell resources={[thing(meta(scalar)), ITEM]}>
    <FormView resource="probe.Thing" id="thing-1" fields={[{ name: "title", label: "Title", widget: "text" }, field]}
      acknowledgedSource={{ record: { id: "thing-1", title: "First", f: value }, values: { title: "First", f: value } } as never}
      submit={async () => refused() as never} />
  </Shell>);
  fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "Changed" } });
  const save = await screen.findByRole("button", { name: "Save" });
  save.focus();
  recorder = recordFocusCalls();
  fireEvent.click(save);
  await screen.findByText(REASON);
  return recorder.calls;
}

describe("a record form's refused save, unchanged (its fields never lock)", () => {
  test.each([
    ["a text field", { name: "f", label: "Field", widget: "text" }, "abc", textbox],
    ["a select", { name: "f", label: "Field", widget: "select", options: OPTIONS }, "a", combobox],
  ] as const)("brings focus to %s's own control", async (_name, field, value, control) => {
    await refuseOnRecord(field as FieldDescriptor, value);
    await waitFor(() => expect(document.activeElement).toBe(control(document.body)));
    expect(document.activeElement?.getAttribute("aria-invalid")).toBe("true");
  });
});
