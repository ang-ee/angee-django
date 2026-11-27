// @vitest-environment happy-dom
import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, describe, expect, test } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders, recordFocusCalls, type RecordedFocusCall } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { FormView } from "./FormView";
import { useFormViewSurface, type FormViewSurface, type UseFormViewSurfaceProps } from "./form-view-surface";
import { invalidFormSubmit } from "./validation-errors";
import { Field, Group, type ActionDescriptor } from "../page";

// Where a refused save brings the person on a record form whose fields sit in a sheet, a collapsed
// sheet section, three pane groups and the trailing lines, beside two saved-record tabs (one kept
// mounted). Each row presses the real Save (or a record verb), records every focus() call with the
// state of its target at that moment, and waits until the refusal hook's own fallback timer has run.
//
// happy-dom computes no app stylesheet, so a pane or section is hidden here only by the `hidden` and
// `inert` attributes its owner sets; `recordFocusCalls` judges each call by those. The closed
// section's row asks the form library's deferred focus while the control is still under the
// section's hidden panel and lands because React renders the section open before that zero-delay
// timer fires: these tests show the order under Node's scheduler, and a browser check, which these
// tests do not make, shows it in a browser.

const meta = (scalar: string) => ({
  kind: "scalar", scalar, readable: true, filterable: false, sortable: false, aggregatable: false, groupable: false,
  creatable: true, updatable: true, requiredOnCreate: false,
});
const FIELDS = ["title", "name", "detail", "alpha_field", "beta_field", "gamma_field"];
const THING = testDataResource("probe.Thing", {
  createFields: FIELDS,
  fields: [{ name: "id", ...meta("ID") }, ...FIELDS.map((name) => ({ name, ...meta("String") }))] as never,
  linesResource: {
    field: "lines", modelLabel: "probe.Line", positionField: "position",
    fields: [{ name: "label", ...meta("String") }, { name: "quantity", ...meta("Int") }, { name: "position", ...meta("Int") }] as never,
  } as never,
});
const RECORD = {
  id: "thing-1", title: "First", name: "Ann", detail: "Deep", alpha_field: "A", beta_field: "B", gamma_field: "G",
  lines: [{ id: "line-1", label: "Alpha line", quantity: 1, position: 0 }],
};
const { Provider, createClient, clearClients } = createUiTestProviders({ resources: [THING], apiUrl: "test://reveal" });

let recorder: ReturnType<typeof recordFocusCalls> | undefined;
afterEach(() => { recorder?.restore(); recorder = undefined; cleanup(); clearClients(); });

function Shell({ children, record = RECORD }: { children: React.ReactNode; record?: typeof RECORD }) {
  const [state] = React.useState(() => ({
    provider: {
      getOne: async () => ({ data: structuredClone(record) }),
      getMany: async () => ({ data: [] }),
      getList: async () => ({ data: [], total: 0 }),
    },
    client: createClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }),
    router: createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) }),
  }));
  return <Provider resources={[THING] as never} dataProvider={state.provider as never} queryClient={state.client}>
    <RouterContextProvider router={state.router}><ModalsHost><ToastProvider>
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>{children}</AppRuntimeProvider>
    </ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>;
}

const REASON = (path: string) => `${path} is refused.`;
const FORM_REASON = "The record cannot be saved now.";
const APPROVE: ActionDescriptor = { id: "approve", label: "Approve", set: { alpha_field: "Approved" } };

type Answer = ReturnType<typeof invalidFormSubmit>;
const answerFor = (refused: readonly string[], formReason = false): Answer => invalidFormSubmit({
  fieldErrors: Object.fromEntries(refused.map((path) => [path, [REASON(path)]])),
  formErrors: formReason ? [FORM_REASON] : [],
});

/** The declarations of the record form: its props without the answer its submit gives; `required` names the fields its own check requires. */
function recordFormProps({ create = false, answer, required = [] }: {
  create?: boolean; answer: () => Answer; required?: readonly string[];
}): UseFormViewSurfaceProps {
  const req = (name: string) => required.includes(name);
  return {
    resource: "probe.Thing",
    id: create ? null : "thing-1",
    submit: async () => answer() as never,
    linesTabLabel: "Lines",
    actions: [APPROVE],
    recordTabs: [
      { id: "notes", label: "Notes", render: ({ focusField }) => <button type="button" onClick={() => focusField("alpha_field")}>Reveal alpha</button> },
      { id: "kept", label: "Kept", keepMounted: true, render: () => <input aria-label="Kept draft" defaultValue="kept" /> },
    ],
    children: [
      <Group key="sheet" label="Main"><Field name="title" label="Title" widget="text" /><Field name="name" label="Name" widget="text" required={req("name")} /></Group>,
      <Group key="details" label="Details" collapsible defaultOpen={false}><Field name="detail" label="Detail" widget="text" required={req("detail")} /></Group>,
      <Group key="alpha" label="Alpha" pane="alpha"><Field name="alpha_field" label="Alpha field" widget="text" required={req("alpha_field")} /></Group>,
      <Group key="beta" label="Beta" pane="beta"><Field name="beta_field" label="Beta field" widget="text" required={req("beta_field")} /></Group>,
      <Group key="gamma" label="Gamma" pane="gamma"><Field name="gamma_field" label="Gamma field" widget="text" /></Group>,
    ],
  };
}

/** Each refused path's own control, the element marked invalid and the one that takes focus. */
const CONTROLS: Readonly<Record<string, () => HTMLElement | null>> = {
  name: () => screen.queryAllByLabelText("Name")[0] ?? null,
  detail: () => screen.queryAllByLabelText("Detail")[0] ?? null,
  alpha_field: () => screen.queryAllByLabelText("Alpha field")[0] ?? null,
  beta_field: () => screen.queryAllByLabelText("Beta field")[0] ?? null,
  "lines.0.label": () => screen.queryAllByDisplayValue("Alpha line")[0] ?? null,
};
const selectedPane = () => document.querySelector("[role='tab'][aria-selected='true']")?.textContent ?? null;
const banner = () => [...document.querySelectorAll<HTMLElement>("[role='alert']")].find((node) => node.textContent?.includes("Save failed")) ?? null;
const detailsTrigger = () => screen.getByRole("button", { name: "Details" });
/** One more zero-delay timer: the refusal hook's fallback, queued right after the form library's focus timer, has run. */
const afterTheTimers = () => act(() => new Promise<void>((resolve) => { setTimeout(resolve); }));

async function selectPane(name: string) {
  fireEvent.click(screen.getByRole("tab", { name }));
  await waitFor(() => expect(selectedPane()).toBe(name));
}

/**
 * Render the record form, open `visit` first when given, select `select`, press the real Save
 * (Create, or the record verb Approve), its button holding focus first as a click leaves it, and
 * return the focus calls from then on.
 */
async function refuse({ refused, visit, select, create = false, formReason = false, via = "save" }: {
  refused: readonly string[]; visit?: string; select?: string; create?: boolean; formReason?: boolean; via?: "save" | "action";
}): Promise<readonly RecordedFocusCall[]> {
  const answer = answerFor(refused, formReason);
  render(<Shell><FormView {...recordFormProps({ create, answer: () => answer })}
    recordExtras={({ focusField }) => <button type="button" onClick={() => focusField("detail")}>Reveal detail</button>} /></Shell>);
  const title = await screen.findByLabelText("Title");
  if (!create) {
    await waitFor(() => expect((title as HTMLInputElement).value).toBe("First"));
    await screen.findByDisplayValue("Alpha line");
  }
  if (visit) await selectPane(visit);
  if (select) await selectPane(select);
  // A record verb acts on the saved record, so its form stays clean; Save shows for an edit.
  if (!create && via === "save") fireEvent.change(title, { target: { value: "Changed" } });
  const trigger = await screen.findByRole("button", { name: via === "action" ? "Approve" : create ? "Create" : "Save" });
  trigger.focus();
  recorder = recordFocusCalls();
  fireEvent.click(trigger);
  return recorder.calls;
}

/** Each focus() call, its target named by what it is to the row, with whether it took focus. */
const focusCalls = (calls: readonly RecordedFocusCall[], field?: HTMLElement | null) => calls.map((call) => [
  call.target === field ? "the field" : call.target === banner() ? "the banner" : call.target.outerHTML.slice(0, 80),
  call.takesFocus,
]);

/** The refused control has focus after the timers, every focus() call went to it and took, none to the banner. */
async function expectFocusedField(calls: readonly RecordedFocusCall[], path: string) {
  const control = CONTROLS[path]!;
  await waitFor(() => expect(document.activeElement).toBe(control()));
  await afterTheTimers();
  const target = control()!;
  expect(document.activeElement).toBe(target);
  expect(focusCalls(calls, target)).toEqual([["the field", true]]);
  expect(target.isConnected).toBe(true);
  expect(target.getAttribute("aria-invalid")).toBe("true");
}

/** The banner has focus after the timers, and it is the only target any focus() call had. */
async function expectFocusedBanner(calls: readonly RecordedFocusCall[]) {
  await waitFor(() => expect(document.activeElement).toBe(banner()));
  await afterTheTimers();
  expect(document.activeElement).toBe(banner());
  expect(focusCalls(calls)).toEqual([["the banner", true]]);
}

describe("a refused save brings the person to its first refused field, selecting the pane that holds it", () => {
  test.each([
    ["1: a sheet field, the lines selected: the pane stays", { refused: ["name"], select: "Lines" }, "name", "Lines"],
    ["2: a pane group's field, its pane selected", { refused: ["alpha_field"], select: "Alpha" }, "alpha_field", "Alpha"],
    ["3: a pane group's field, another pane group selected", { refused: ["alpha_field"], select: "Beta" }, "alpha_field", "Alpha"],
    // The form library keeps the control of a pane opened earlier after that pane unmounts: only the mounted one is asked.
    ["3b: a pane group's field, its pane opened earlier and another selected", { refused: ["alpha_field"], visit: "Alpha", select: "Beta" }, "alpha_field", "Alpha"],
    ["4: a pane group's field, a saved-record tab selected", { refused: ["alpha_field"], select: "Notes" }, "alpha_field", "Alpha"],
    ["5: a pane group's field, a saved-record tab kept mounted selected", { refused: ["alpha_field"], select: "Kept" }, "alpha_field", "Alpha"],
    ["6: a pane group's field, the trailing lines selected", { refused: ["alpha_field"], select: "Lines" }, "alpha_field", "Alpha"],
    ["8: a line member, a pane group selected: the lines pane", { refused: ["lines.0.label"], select: "Alpha" }, "lines.0.label", "Lines"],
    ["10: a pane group's field on a create form, another pane selected", { refused: ["alpha_field"], select: "Beta", create: true }, "alpha_field", "Alpha"],
    ["11: a sheet field declared before a refused pane group's field: the sheet field, the pane stays", { refused: ["name", "alpha_field"], select: "Beta" }, "name", "Beta"],
    ["12: two pane groups' fields, a third pane selected: the first one's pane", { refused: ["alpha_field", "beta_field"], select: "Gamma" }, "alpha_field", "Alpha"],
    ["14: a record verb's refusal of a pane group's field, another pane selected", { refused: ["alpha_field"], select: "Beta", via: "action" as const }, "alpha_field", "Alpha"],
  ])("%s", async (_row, spec, focused, pane) => {
    const calls = await refuse(spec);
    await expectFocusedField(calls, focused);
    expect(selectedPane()).toBe(pane);
  });

  test("7: a field of a closed sheet section opens the section and takes focus; the pane stays, and the section a later reveal asks for opens again", async () => {
    const calls = await refuse({ refused: ["detail"], select: "Lines" });
    await expectFocusedField(calls, "detail");
    expect(detailsTrigger().getAttribute("aria-expanded")).toBe("true");
    expect(selectedPane()).toBe("Lines");
    // The refusal's reveal named the field to the sections and then let it go: closed by hand, the
    // section opens again for the next reveal of the same field.
    fireEvent.click(detailsTrigger());
    await waitFor(() => expect(detailsTrigger().getAttribute("aria-expanded")).toBe("false"));
    const reveal = screen.getByRole("button", { name: "Reveal detail" });
    reveal.focus();
    recorder?.restore();
    const again = recorder = recordFocusCalls();
    fireEvent.click(reveal);
    await waitFor(() => expect(detailsTrigger().getAttribute("aria-expanded")).toBe("true"));
    await waitFor(() => expect(document.activeElement).toBe(CONTROLS.detail!()));
    expect(focusCalls(again.calls, CONTROLS.detail!())).toEqual([["the field", true]]);
  });

  test("7b: a record verb refused twice for a closed section's field, the section closed by hand in between, opens it and focuses the field again", async () => {
    const calls = await refuse({ refused: ["detail"], select: "Beta", via: "action" });
    await expectFocusedField(calls, "detail");
    fireEvent.click(detailsTrigger());
    await waitFor(() => expect(detailsTrigger().getAttribute("aria-expanded")).toBe("false"));
    // The field's error is the same as before, so the section does not open for it: the refusal's own naming of the field does.
    const approve = screen.getByRole("button", { name: "Approve" });
    approve.focus();
    recorder?.restore();
    const again = recorder = recordFocusCalls();
    fireEvent.click(approve);
    await waitFor(() => expect(detailsTrigger().getAttribute("aria-expanded")).toBe("true"));
    await expectFocusedField(again.calls, "detail");
    expect(selectedPane()).toBe("Beta");
  });

  test("guard: 9: the lines refused as a whole select the lines pane, and with no control of their own to focus the banner takes focus", async () => {
    const calls = await refuse({ refused: ["lines"], select: "Alpha" });
    await expectFocusedBanner(calls);
    expect(selectedPane()).toBe("Lines");
  });

  test.fails("defect: 9: the lines refused as a whole show their reason nowhere and the banner names them by their path, since no line control claims the lines field's own error; it should show the reason with the lines", async () => {
    await refuse({ refused: ["lines"], select: "Alpha" });
    await waitFor(() => expect(banner()).not.toBeNull());
    await afterTheTimers();
    expect(screen.getByText(REASON("lines"))).toBeTruthy();
  });

  test.each([
    ["3", "Beta"], ["4", "Notes"], ["5", "Kept"], ["6", "Lines"],
  ])("13 (row %s with a form-level reason as well): the banner takes focus and the selected pane, %s, stays", async (_row, select) => {
    const calls = await refuse({ refused: ["alpha_field"], select, formReason: true });
    await expectFocusedBanner(calls);
    expect(banner()?.textContent).toContain(FORM_REASON);
    expect(selectedPane()).toBe(select);
    expect(CONTROLS.alpha_field!()).toBeNull();
  });

  // The trailing lines select their own pane after any failed save that touches them
  // (`FormViewLinesPane`), beside the reveal: the next two rows are where the two meet.
  test("guard: a line member refused with a form-level reason as well: the banner takes focus, and the lines select their own pane", async () => {
    const calls = await refuse({ refused: ["lines.0.label"], select: "Alpha", formReason: true });
    await expectFocusedBanner(calls);
    expect(banner()?.textContent).toContain(FORM_REASON);
    expect(selectedPane()).toBe("Lines");
  });

  test.fails("defect: a pane group's field refused together with a line member ends on the lines pane with focus on the banner, since the lines select their own pane after the reveal selected the field's; it should end on the first refused field, in its pane", async () => {
    const calls = await refuse({ refused: ["alpha_field", "lines.0.label"], select: "Beta" });
    await expectFocusedField(calls, "alpha_field");
    expect(selectedPane()).toBe("Alpha");
  });
});

describe("the form's own check brings its first errored field into sight too, and the form library then focuses it", () => {
  test.each<[row: string, required: readonly string[], visit: string | undefined, select: string, pane: string]>([
    ["a required pane group's field left empty, another pane group selected", ["alpha_field"], undefined, "Beta", "Alpha"],
    ["the same, its pane opened earlier", ["alpha_field"], "Alpha", "Beta", "Alpha"],
    ["a required field of a closed section left empty: the section opens and the pane stays", ["detail"], undefined, "Lines", "Lines"],
    ["two required pane groups' fields left empty, a third pane selected: the first one's pane", ["alpha_field", "beta_field"], undefined, "Gamma", "Alpha"],
  ])("%s", async (_row, required, visit, select, pane) => {
    let submitted = 0;
    const record = { ...RECORD, ...Object.fromEntries(required.map((name) => [name, ""])) };
    render(<Shell record={record}><FormView {...recordFormProps({ required, answer: () => { submitted += 1; return answerFor([]); } })} /></Shell>);
    const title = await screen.findByLabelText("Title");
    await waitFor(() => expect((title as HTMLInputElement).value).toBe("First"));
    await screen.findByDisplayValue("Alpha line");
    if (visit) await selectPane(visit);
    await selectPane(select);
    fireEvent.change(title, { target: { value: "Changed" } });
    const save = await screen.findByRole("button", { name: "Save" });
    save.focus();
    const { calls } = recorder = recordFocusCalls();
    fireEvent.click(save);
    const control = CONTROLS[required[0]!]!;
    await waitFor(() => expect(document.activeElement).toBe(control()));
    await afterTheTimers();
    expect(document.activeElement).toBe(control());
    expect(control()!.getAttribute("aria-invalid")).toBe("true");
    expect(selectedPane()).toBe(pane);
    // The form library asks its focus at once and again on its timer: the call that lands is on the mounted control.
    expect(focusCalls(calls, control()).at(-1)).toEqual(["the field", true]);
    expect(calls.some((call) => call.target === banner())).toBe(false);
    // The check stops the save before any request.
    expect(submitted).toBe(0);
  });
});

test("15: a saved-record tab's focusField of a pane group's field selects that pane, then focuses the field", async () => {
  render(<Shell><FormView {...recordFormProps({ answer: () => answerFor([]) })} /></Shell>);
  await screen.findByDisplayValue("Alpha line");
  await selectPane("Notes");
  const reveal = await screen.findByRole("button", { name: "Reveal alpha" });
  reveal.focus();
  const { calls } = recorder = recordFocusCalls();
  fireEvent.click(reveal);
  await waitFor(() => expect(document.activeElement).toBe(CONTROLS.alpha_field!()));
  expect(focusCalls(calls, CONTROLS.alpha_field!())).toEqual([["the field", true]]);
  expect(selectedPane()).toBe("Alpha");
});

/** The surface beside no FormView: its selected pane and the path it names to its sections, at each commit. */
function SurfaceProbe({ seen, surfaceRef, ...props }: UseFormViewSurfaceProps & {
  seen: [string | undefined, string | null][];
  surfaceRef: { current: FormViewSurface | null };
}): null {
  const surface = useFormViewSurface(props);
  surfaceRef.current = surface;
  React.useEffect(() => { seen.push([surface.activeRecordTab, surface.requestedFocusPath]); });
  return null;
}

/** One row of the surface's own reveal: what is refused, the pane selected first, and how the refusal arrives. */
interface SurfaceRefusal {
  refused: readonly string[];
  select: string;
  create?: boolean;
  formReason?: boolean;
  via?: "save" | "action";
}

describe("the surface's reveal of a refused field names it to its sections and then lets it go", () => {
  test.each<[row: string, refusal: SurfaceRefusal, named: string | null, pane: string]>([
    ["1: a sheet field", { refused: ["name"], select: "beta" }, "name", "beta"],
    ["3: a pane group's field", { refused: ["alpha_field"], select: "beta" }, "alpha_field", "alpha"],
    ["7: a field of a closed section", { refused: ["detail"], select: "beta" }, "detail", "beta"],
    ["8: a line member", { refused: ["lines.0.label"], select: "alpha" }, "lines.0.label", "editable-lines"],
    ["9: the lines as a whole", { refused: ["lines"], select: "alpha" }, "lines", "editable-lines"],
    ["10: a pane group's field on a create form", { refused: ["alpha_field"], select: "beta", create: true }, "alpha_field", "alpha"],
    ["12: two pane groups' fields", { refused: ["alpha_field", "beta_field"], select: "gamma" }, "alpha_field", "alpha"],
    ["13: a pane group's field with a form-level reason", { refused: ["alpha_field"], select: "beta", formReason: true }, null, "beta"],
    ["14: a record verb's refusal of a pane group's field", { refused: ["alpha_field"], select: "beta", via: "action" }, "alpha_field", "alpha"],
  ])("%s", async (_row, { refused, select, create = false, formReason = false, via = "save" }, named, pane) => {
    const seen: [string | undefined, string | null][] = [];
    const surfaceRef: { current: FormViewSurface | null } = { current: null };
    const answer = answerFor(refused, formReason);
    render(<Shell><SurfaceProbe {...recordFormProps({ create, answer: () => answer })} seen={seen} surfaceRef={surfaceRef} /></Shell>);
    if (!create) await waitFor(() => expect(surfaceRef.current?.displayRecord).not.toBeNull());
    act(() => surfaceRef.current!.setActiveRecordTab(select));
    await waitFor(() => expect(surfaceRef.current!.activeRecordTab).toBe(select));
    const before = seen.length;
    await act(async () => {
      if (via === "action") await surfaceRef.current!.applyPatch({ alpha_field: "Approved" });
      else await surfaceRef.current!.submitForm();
    });
    await afterTheTimers();
    const after = seen.slice(before);
    // Named to the sections for a commit or more, then let go.
    expect([...new Set(after.map(([, path]) => path).filter((path) => path !== null))]).toEqual(named === null ? [] : [named]);
    expect(after.at(-1)).toEqual([pane, null]);
    expect(surfaceRef.current!.requestedFocusPath).toBeNull();
  });
});
