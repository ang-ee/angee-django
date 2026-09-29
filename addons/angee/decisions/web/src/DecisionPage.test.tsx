// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { useFormContext } from "react-hook-form";
import type { GetOneParams } from "@refinedev/core";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { ShellPageTestProviders } from "@angee/app/testing";
import { defaultWidgets, Input, ToastProvider, type SlotContribution, type WidgetMap, type WidgetRenderProps } from "@angee/ui";
import { createRouteHref } from "@angee/ui/runtime";
import { createUiTestProviders } from "@angee/ui/testing";
import type { ActionOutcome } from "@angee/refine";

import { DecisionPage, DecisionReview } from "./DecisionPage";
import { DecisionDocument, type Decision, type DecisionSeat } from "./documents.console";
import { decisionFixture, decisionResourceFixture, decisionSeatFixture, decisionSubjectFixture } from "./testing";
import { decisionContent } from "./slots";

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://decision-review",
  resources: [decisionResourceFixture, decisionSubjectFixture],
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
  dataProvider: { getOne: async ({ id }: Partial<GetOneParams>) => ({ data: { id, display_name: "Review note" } }) },
});

const mocks = vi.hoisted(() => ({
  decide: vi.fn<(...args: unknown[]) => Promise<ActionOutcome>>(),
  refetch: vi.fn(),
  detail: { data: undefined as { decisions_by_pk: Decision | null } | undefined, isLoading: false, error: null as Error | null },
  seats: { data: { decisions: [] as DecisionSeat[] }, isLoading: false, error: null as Error | null },
}));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: (document: unknown) => document === DecisionDocument
    ? { ...mocks.detail, refetch: mocks.refetch }
    : { ...mocks.seats, refetch: vi.fn() },
}));
vi.mock("@angee/ui", async (importOriginal) => {
  const { createUiRouteTestDoubles, createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal, createUiRouteTestDoubles(), {
    useRouteParam: () => "dcn_review",
    useActionOutcomeMutation: () => [mocks.decide, { fetching: false, error: null }],
  });
});
beforeEach(() => {
  mocks.detail = { data: { decisions_by_pk: decisionFixture() }, isLoading: false, error: null };
  mocks.seats = { data: { decisions: [] }, isLoading: false, error: null };
  mocks.decide.mockResolvedValue({ ok: true, message: "Recorded" });
  mocks.refetch.mockResolvedValue({ data: mocks.detail.data });
});
afterEach(() => { cleanup(); clearClients(); vi.clearAllMocks(); });

function renderReview(decision = decisionFixture(), reload = vi.fn().mockResolvedValue(decision), widgets: WidgetMap = defaultWidgets, slots: readonly SlotContribution[] = []) {
  return { reload, ...render(<Provider><ShellPageTestProviders runtime={{ widgets, slots,
    routeHref: createRouteHref([{ name: "notes.record", path: "/notes/$id" }]),
    routesByResource: { "notes.Note": { collection: "notes", record: { name: "notes.record", param: "id" } } },
  }}><ToastProvider>
    <DecisionReview decision={decision} reload={reload} />
  </ToastProvider></ShellPageTestProviders></Provider>) };
}

function DraftWidget({ value, field, onChange, onValidityChange }: WidgetRenderProps<string>) {
  const [draft, setDraft] = useState(value ?? "");
  return <Input {...field?.controlProps} aria-label="Draft note" value={draft} onChange={(event) => {
    const next = event.currentTarget.value;
    setDraft(next);
    const valid = !next.startsWith("?");
    onValidityChange?.(valid);
    if (valid) onChange?.(next);
  }} />;
}
function ExtensionField() {
  const form = useFormContext<{ extensionNote: string }>();
  return <Input aria-label="Extension note" {...form.register("extensionNote")} />;
}
async function chooseAction(label: string) {
  fireEvent.click(screen.getByRole("combobox", { name: "Action" }));
  const option = await screen.findByRole("option", { name: label });
  fireEvent.pointerDown(option, { pointerType: "mouse" });
  fireEvent.click(option);
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Action" }).textContent).toContain(label));
}

describe("decision review", () => {
  test("applies initial values, shows immutable values and sends action separately from values", async () => {
    renderReview();
    expect((screen.getByRole("textbox", { name: "Note" }) as HTMLInputElement).value).toBe("Read");
    expect(screen.getByText("R-7")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Reference" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: /Reason/ })).toBeNull();
    expect((await screen.findByRole("link", { name: "Review note" })).getAttribute("href")).toBe("/notes/nte_7");
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    await waitFor(() => expect(mocks.decide).toHaveBeenCalledWith("dcn_review", {
      revision: 3, action: "accept", values: { note: "Read", reference: "R-7" },
    }));
  });

  test("keeps keyboard focus on the same action control when the branch changes", async () => {
    renderReview();
    const action = screen.getByRole("combobox", { name: "Action" });
    action.focus();
    await chooseAction("Reject");
    await waitFor(() => expect(document.activeElement).toBe(action));
    expect(screen.getByRole("combobox", { name: "Action" })).toBe(action);
    expect(screen.getByRole("textbox", { name: /Reason/ })).toBeTruthy();
  });

  test("preserves unrelated extension values while replacing branch values", async () => {
    renderReview(decisionFixture(), undefined, defaultWidgets, [decisionContent("review", ExtensionField)]);
    const extension = screen.getByRole("textbox", { name: "Extension note" }) as HTMLInputElement;
    fireEvent.change(extension, { target: { value: "Retain this draft" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Note" }), { target: { value: "Replace this branch draft" } });
    await chooseAction("Reject");
    expect(extension.value).toBe("Retain this draft");
    await chooseAction("Accept");
    expect(extension.value).toBe("Retain this draft");
    expect((screen.getByRole("textbox", { name: "Note" }) as HTMLInputElement).value).toBe("Read");
  });

  test("renders an open decision read-only when the viewer cannot act", () => {
    renderReview(decisionFixture({ is_open: true, can_act: false }));
    expect(screen.getByText("Read")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Note" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Action" })).toBeNull();
    expect(screen.getByRole("button", { name: "Decide" }).hasAttribute("disabled")).toBe(true);
    const form = screen.getByRole("form", { name: "Decision" });
    expect(form.querySelector("fieldset")?.disabled).toBe(true);
    fireEvent.submit(form);
    expect(mocks.decide).not.toHaveBeenCalled();
  });

  test("keeps a successful answer successful without awaiting a failing reload", async () => {
    const { reload } = renderReview(decisionFixture(), vi.fn().mockRejectedValue(new Error("Refresh failed")));
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    expect(await screen.findByText("Decision recorded.")).toBeTruthy();
    expect(mocks.decide).toHaveBeenCalledOnce();
    expect(reload).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("validates only the selected branch with readable errors associated with the actual input", async () => {
    renderReview();
    await chooseAction("Reject");
    const reason = screen.getByRole("textbox", { name: /Reason/ });
    fireEvent.change(reason, { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    const message = await screen.findByText("Enter at least 3 characters.");
    const invalidReason = screen.getByRole("textbox", { name: /Reason/ });
    expect(invalidReason.getAttribute("aria-invalid")).toBe("true");
    expect(invalidReason.getAttribute("aria-describedby")?.split(" ").some((id) => document.getElementById(id)?.contains(message))).toBe(true);
    expect(mocks.decide).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox", { name: "Note" })).toBeNull();
    await chooseAction("Accept");
    expect(screen.queryByText("Enter at least 3 characters.")).toBeNull();
    expect(screen.queryByRole("textbox", { name: /Reason/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    await waitFor(() => expect(mocks.decide).toHaveBeenCalledOnce());
  });

  test("discards a branch's local invalid draft when the next branch reuses its field, widget and default", async () => {
    renderReview(decisionFixture({ form_schema: {
      type: "object", properties: { action: { type: "string", enum: ["accept", "reject"],
        options: [{ value: "accept", label: "Accept" }, { value: "reject", label: "Reject" }] } },
      required: ["action"], discriminator: { propertyName: "action" },
      oneOf: ["accept", "reject"].map((action) => ({
        type: "object", additionalProperties: false, required: ["action", "note"], properties: {
          action: { type: "string", const: action },
          note: { type: "string", widget: "angee.decisions.draft", title: "Note", default: "Read" },
        },
      })),
    } }), undefined, { ...defaultWidgets, "angee.decisions.draft": {
      edit: DraftWidget, read: ({ value }: WidgetRenderProps<string>) => <span>{value}</span>,
    } });
    fireEvent.change(screen.getByRole("textbox", { name: "Draft note" }), { target: { value: "?unfinished" } });
    expect((screen.getByRole("textbox", { name: "Draft note" }) as HTMLInputElement).value).toBe("?unfinished");
    await chooseAction("Reject");
    expect((screen.getByRole("textbox", { name: "Draft note" }) as HTMLInputElement).value).toBe("Read");
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    await waitFor(() => expect(mocks.decide).toHaveBeenCalledWith("dcn_review", {
      revision: 3, action: "reject", values: { note: "Read" },
    }));
  });

  test("keeps the draft and binds server errors while advancing the rejected attempt's revision", async () => {
    const { reload } = renderReview(decisionFixture(), vi.fn().mockResolvedValue(decisionFixture({ revision: 4 })));
    mocks.decide.mockResolvedValueOnce({ ok: false, message: "Check the answer.", validationErrors: { note: ["Add the missing detail."] } });
    const note = screen.getByRole("textbox", { name: "Note" });
    fireEvent.change(note, { target: { value: "Draft answer" } });
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Note" }).getAttribute("aria-invalid")).toBe("true"));
    const invalidNote = screen.getByRole("textbox", { name: "Note" });
    expect(screen.getAllByText(/Add the missing detail\./)).toHaveLength(1);
    expect((invalidNote as HTMLInputElement).value).toBe("Draft answer");
    expect(reload).toHaveBeenCalledOnce();
    fireEvent.change(invalidNote, { target: { value: "Corrected answer" } });
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    await waitFor(() => expect(mocks.decide).toHaveBeenLastCalledWith("dcn_review", {
      revision: 4, action: "accept", values: { note: "Corrected answer", reference: "R-7" },
    }));
  });

  test("retains server field errors when refreshing a rejected answer fails", async () => {
    const { reload } = renderReview(decisionFixture(), vi.fn().mockRejectedValue(new Error("Refresh failed")));
    mocks.decide.mockResolvedValueOnce({ ok: false, message: "Check the answer.", validationErrors: { note: ["Add the missing detail."] } });
    fireEvent.change(screen.getByRole("textbox", { name: "Note" }), { target: { value: "Draft answer" } });
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    await screen.findByText("Add the missing detail.");
    expect(screen.getAllByText(/Add the missing detail\./)).toHaveLength(1);
    expect((screen.getByRole("textbox", { name: "Note" }) as HTMLInputElement).value).toBe("Draft answer");
    expect(reload).toHaveBeenCalledOnce();
    expect(screen.queryByText("Refresh failed")).toBeNull();
  });

  test("requires explicit reload after conflict and submits against the refreshed revision", async () => {
    const { reload } = renderReview(decisionFixture(), vi.fn().mockResolvedValue(decisionFixture({ revision: 7 })));
    mocks.decide.mockResolvedValueOnce({ ok: false, message: "Could not decide.", validationErrors: { revision: ["Changed"] } });
    fireEvent.change(screen.getByRole("textbox", { name: "Note" }), { target: { value: "Old draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    expect((await screen.findByRole("alert")).textContent).toContain("This decision has changed. Reload before submitting again.");
    expect(screen.getByRole("button", { name: "Decide" }).hasAttribute("disabled")).toBe(true);
    expect(reload).not.toHaveBeenCalled();
    expect(screen.queryByRole("combobox", { name: "Action" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Note" })).toBeNull();
    expect(screen.getByRole("form", { name: "Decision" }).querySelector("fieldset")?.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Decide" }).hasAttribute("disabled")).toBe(false));
    expect((screen.getByRole("textbox", { name: "Note" }) as HTMLInputElement).value).toBe("Read");
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    await waitFor(() => expect(mocks.decide).toHaveBeenLastCalledWith("dcn_review", {
      revision: 7, action: "accept", values: { note: "Read", reference: "R-7" },
    }));
  });

  test("a failed conflict reload keeps submitting blocked", async () => {
    const { reload } = renderReview(decisionFixture(), vi.fn().mockRejectedValue(new Error("Refresh failed")));
    mocks.decide.mockResolvedValueOnce({ ok: false, message: "Could not decide.", validationErrors: { revision: ["Changed"] } });
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    fireEvent.click(await screen.findByRole("button", { name: "Reload" }));
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
    expect(screen.getByRole("button", { name: "Decide" }).hasAttribute("disabled")).toBe(true);
    expect(screen.queryByRole("combobox", { name: "Action" })).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("This decision has changed.");
  });

  test("renders a settled resolution with resolver and time as a read-only form", () => {
    renderReview(decisionFixture({ is_open: false, can_act: false, verdict: "COMPLETED", closed_reason: "RESOLVED", resolution: { action: "accept", note: "Final answer", reference: "R-7" },
      resolved_by: { display_name: "Reviewer" }, resolved_at: "2026-09-29T09:30:00Z" }));
    expect(screen.getByText("Final answer")).toBeTruthy();
    expect(screen.getAllByText("Completed").length).toBeGreaterThan(0);
    expect(screen.getByText("Reviewer")).toBeTruthy();
    expect(screen.getByText("Resolved at")).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Decide" })).toBeNull();
  });

  test("shows visible other seats with their state and excludes this seat", () => {
    mocks.seats.data.decisions = [decisionSeatFixture(), decisionSeatFixture({ id: "dcn_other", index: 1, verdict: "REJECTED", closed_reason: "RESOLVED" })];
    renderReview();
    expect(screen.getByRole("link", { name: "Seat 2" }).getAttribute("href")).toContain("dcn_other");
    expect(screen.queryByRole("link", { name: "Seat 1" })).toBeNull();
    expect(screen.getByText("Seats you have permission to read.")).toBeTruthy();
    expect(screen.getByText(/Reviewer.*Rejected/)).toBeTruthy();
  });

  test("shows a failed seat query without discarding the form", () => {
    mocks.seats.error = new Error("Seat query failed");
    renderReview();
    expect(screen.getByRole("alert").textContent).toContain("This decision is unavailable.");
    expect(screen.getByRole("textbox", { name: "Note" })).toBeTruthy();
  });

  test("rejects malformed frozen forms with a readable message", () => {
    renderReview(decisionFixture({ form_schema: { type: "object", properties: {} } }));
    expect(screen.getByRole("alert").textContent).toContain("This decision’s form is unavailable.");
  });
});

describe("decision page request states", () => {
  function renderPage() { return render(<Provider><ShellPageTestProviders runtime={{ widgets: defaultWidgets }}><ToastProvider><DecisionPage /></ToastProvider></ShellPageTestProviders></Provider>); }
  test("renders a loading state before the first response", () => {
    mocks.detail = { data: undefined, isLoading: true, error: null };
    renderPage();
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.queryByRole("form")).toBeNull();
  });
  test("renders a missing decision", () => {
    mocks.detail.data = { decisions_by_pk: null };
    renderPage();
    expect(screen.getByText("This decision is unavailable.")).toBeTruthy();
  });
  test("offers retry for a failed request", () => {
    mocks.detail = { data: undefined, isLoading: false, error: new Error("Query failed") };
    renderPage();
    fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Reload" }));
    expect(mocks.refetch).toHaveBeenCalledOnce();
  });
});
