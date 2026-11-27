// @vitest-environment happy-dom

import type * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  // The shared picker stands in as its trigger, which takes the form's focus through `controlRef`
  // and, as the real picker's trigger is, is disabled while read-only (a dialog's fields lock as it submits).
  SubjectPicker: ({ controlRef, id, readOnly, "aria-invalid": invalid }: {
    controlRef?: React.Ref<HTMLButtonElement>; id?: string; readOnly?: boolean; "aria-invalid"?: boolean;
  }) => <button type="button" ref={controlRef} id={id} disabled={readOnly} aria-label="Principal" aria-invalid={invalid}>Principal</button>,
}));

import { AppRuntimeProvider, ModalsHost, MutationDialog, ToastProvider, defaultWidgets, invalidFormSubmit } from "@angee/ui";
import { createUiTestProviders, recordFocusCalls } from "@angee/ui/testing";

import { SubjectControl } from "./SubjectControl";

const { Provider, clearClients } = createUiTestProviders({ router: true });
let recorder: ReturnType<typeof recordFocusCalls> | undefined;
afterEach(() => { recorder?.restore(); recorder = undefined; cleanup(); clearClients(); });

test("a refused subject brings focus to its picker, not the banner", async () => {
  const refused = async () => invalidFormSubmit({ fieldErrors: { subject: ["This subject holds no role here."] }, formErrors: [] });
  render(<Provider><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <MutationDialog open onOpenChange={() => undefined} title="Grant" submitLabel="Grant" parseValues={(values) => values}
      fields={[{ name: "subject", label: "Subject", control: (props) => <SubjectControl {...props} /> }]}
      initialValues={{ subject: "auth/user:1" }} onSubmit={refused} />
  </AppRuntimeProvider></ToastProvider></ModalsHost></Provider>);
  const grant = await screen.findByRole("button", { name: "Grant" }) as HTMLButtonElement;
  await waitFor(() => expect(grant.disabled).toBe(false));
  grant.focus();
  recorder = recordFocusCalls();
  fireEvent.click(grant);
  await screen.findByText("This subject holds no role here.");
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Principal" })));
  expect(document.activeElement?.getAttribute("aria-invalid")).toBe("true");
  // Every focus call reached the picker once it took focus again, and none the banner.
  expect(recorder.calls.length).toBeGreaterThan(0);
  expect(recorder.calls.map((call) => [call.target.getAttribute("aria-label"), call.takesFocus]))
    .toEqual(recorder.calls.map(() => ["Principal", true]));
});
