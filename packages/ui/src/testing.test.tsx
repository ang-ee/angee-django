// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { boundedGraphQLTransportError } from "@angee/refine";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  createMutationDialogTestDouble,
  createUiRouteTestDoubles,
  createUiTestModule,
  createUiTestProviders,
  recordFocusCalls,
} from "./testing";
import { Tooltip } from "./ui/tooltip";
import type { KeyedMutationDialogProps } from "./views/form/MutationDialog";
import { formSubmitError } from "./views/form/validation-errors";

describe("shared UI mock contracts", () => {
  test("groups overrides over one imported UI module", async () => {
    const imported = { useRouteSearch: () => ({ original: true }), untouched: "owner" };
    const module = await createUiTestModule(
      async () => imported as never,
      createUiRouteTestDoubles({ search: { decision: "decision-1" } }),
    );

    expect((module as unknown as { untouched: string }).untouched).toBe("owner");
    expect(module.useRouteSearch()).toEqual({ decision: "decision-1" });
  });
});

describe("MutationDialog test double", () => {
  test("validates before it submits, with the dialog's quiet call options, and submits nothing refused", async () => {
    const Dialog = createMutationDialogTestDouble({ values: { name: "Ada" } });
    const validate = vi.fn()
      .mockResolvedValueOnce({ fieldErrors: { name: ["Taken"] }, formErrors: [] })
      .mockResolvedValueOnce(null);
    const onSubmit = vi.fn(async () => ({ status: "ok" as const, data: "saved" }));
    const onSubmitted = vi.fn();
    render(<Dialog open title="Create" fields={[]} submitLabel="Create" parseValues={(values) => values}
      validate={validate} onSubmit={onSubmit} onSubmitted={onSubmitted} />);

    fireEvent.click(screen.getByRole("button", { name: "Submit mutation dialog" }));
    await waitFor(() => expect(validate).toHaveBeenCalledExactlyOnceWith({ name: "Ada" }, { errorNotification: false }));
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Submit mutation dialog" }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalledExactlyOnceWith("saved", { name: "Ada" }));
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ name: "Ada" }, { errorNotification: false });
    cleanup();
  });

  test.each([
    ["a refusal", () => ({ status: "invalid" as const, issues: { fieldErrors: {}, formErrors: ["Taken"] } })],
    ["a lost answer returned as a result", () => formSubmitError(boundedGraphQLTransportError(new TypeError("Failed to fetch")))],
  ])("a keyed double sends one key per draft, as the dialog's key owner holds it: kept through %s, ended by an accepted answer", async (_name, failure) => {
    const Dialog = createMutationDialogTestDouble({ values: { name: "Ada" } });
    const keys: string[] = [];
    const answers = [failure(), { status: "ok" as const, data: "saved" }, { status: "ok" as const, data: "again" }];
    // A keyed dialog's props, as a keyed caller renders the double in its place.
    const Keyed = Dialog as unknown as (props: KeyedMutationDialogProps<Record<string, unknown>>) => ReactElement | null;
    render(<Keyed open title="Create" fields={[]} submitLabel="Create" parseValues={(values) => values}
      idempotencyScope="dashboards.create-personal"
      onSubmit={async (_values, _options, key) => { keys.push(key); return answers.shift()!; }} />);
    for (let count = 1; count <= 3; count += 1) {
      fireEvent.click(screen.getByRole("button", { name: "Submit mutation dialog" }));
      await waitFor(() => expect(keys).toHaveLength(count));
    }
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
    cleanup();
  });
});

describe("shared UI providers", () => {
  test("a hovered tooltip opens in the same event, without waiting for its open delay", () => {
    const { Provider, clearClients } = createUiTestProviders();
    try {
      render(<Provider><Tooltip label="Imported source"><button type="button">Source</button></Tooltip></Provider>);
      fireEvent.mouseEnter(screen.getByRole("button", { name: "Source" }));
      expect(screen.getByText("Imported source")).toBeTruthy();
    } finally {
      cleanup();
      clearClients();
    }
  });
});

describe("recordFocusCalls", () => {
  afterEach(() => { document.body.replaceChildren(); });
  // Each row: a focus() target `#t` and the verdict it records. Every verdict was measured in
  // headless Chromium 156: `focus()` on the target, then whether `document.activeElement` is it.
  const rows: readonly [name: string, html: string | null, takesFocus: boolean][] = [
    ["a disabled button", `<button id="t" disabled>x</button>`, false],
    ["a read-only input", `<input id="t" readonly>`, true],
    ["an input inside a disabled fieldset", `<fieldset disabled><input id="t"></fieldset>`, false],
    ["a span with a tabindex inside a disabled fieldset, not a form control", `<fieldset disabled><span id="t" tabindex="-1" aria-disabled="true"></span></fieldset>`, true],
    ["a contenteditable=\"false\" element without a tabindex", `<div id="t" contenteditable="false">x</div>`, false],
    ["a contenteditable=\"true\" element", `<div id="t" contenteditable="true">x</div>`, true],
    ["an editable element under an ancestor computed display: none", `<style>.gone { display: none }</style><div class="gone"><div id="t" contenteditable="true">x</div></div>`, false],
    ["an input outside the document", null, false],
    ["an input inside a disabled fieldset's first legend", `<fieldset disabled><legend><input id="t"></legend></fieldset>`, true],
    ["an input inside a disabled fieldset's second legend", `<fieldset disabled><legend>a</legend><legend><input id="t"></legend></fieldset>`, false],
    ["a button inside [hidden]", `<div hidden><button id="t">x</button></div>`, false],
    ["a button inside [inert]", `<div inert><button id="t">x</button></div>`, false],
    ["a button inside a closed details", `<details><summary>s</summary><button id="t">x</button></details>`, false],
    ["a closed details' summary", `<details><summary id="t">s</summary></details>`, true],
    ["a button inside an open details", `<details open><summary>s</summary><button id="t">x</button></details>`, true],
    ["a details' second summary", `<details open><summary>a</summary><summary id="t">b</summary></details>`, false],
    ["a summary outside a details", `<div><summary id="t">s</summary></div>`, false],
    ["a button computed visibility: hidden", `<div style="visibility: hidden"><button id="t">x</button></div>`, false],
    ["a button computed visibility: collapse", `<div style="visibility: collapse"><button id="t">x</button></div>`, false],
    ["a visible button under an ancestor computed visibility: hidden", `<div style="visibility: hidden"><button id="t" style="visibility: visible">x</button></div>`, true],
    ["an input of type hidden", `<input id="t" type="hidden">`, false],
    ["an anchor without href", `<a id="t">x</a>`, false],
    ["a div without a tabindex", `<div id="t">x</div>`, false],
    ["a contenteditable=\"false\" element with a tabindex", `<div id="t" contenteditable="false" tabindex="0">x</div>`, true],
  ];
  test.each(rows)("%s (%s): takesFocus is %s", (_name, html, takesFocus) => {
    let target: HTMLElement;
    if (html === null) target = document.createElement("input");
    else {
      document.body.innerHTML = html;
      target = document.getElementById("t")!;
    }
    const recorder = recordFocusCalls();
    try {
      target.focus();
      expect(recorder.calls.map((call) => [call.target, call.takesFocus])).toEqual([[target, takesFocus]]);
    } finally {
      recorder.restore();
    }
  });
});
