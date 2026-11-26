// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render as testingRender, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { testDataResource } from "@angee/metadata/testing";

import type { ReactElement } from "react";
import { ToastProvider } from "../../feedback";
import { AppRuntimeProvider, type WidgetMap } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import { lazyWidget } from "../../widgets/lazy-widget";
import {
  LabeledDescriptorField,
  MutationDialog,
  mutationDialogValueCodecs,
  submitMutationDialog,
} from "./MutationDialog";
import { deserializeFormSpec } from "./form-spec";
import { MEMBER_ERRORS, type FormSubmitResult } from "./validation-errors";
import type { DescriptorField } from "./DescriptorFieldList";
import { createUiTestProviders } from "../../testing";

const { Provider, clearClients } = createUiTestProviders({
  queryClientConfig: { defaultOptions: { queries: { retry: false } } },
});

const render = (ui: ReactElement) => testingRender(ui, { wrapper: ToastProvider });

const ok = { status: "ok", data: undefined } as const;
const parseRawValues = (values: Readonly<Record<string, unknown>>) => values;

describe("MutationDialog", () => {
  afterEach(() => { cleanup(); clearClients(); });

  test("an owned trigger opens the dialog and receives focus after dismissal", async () => {
    const submit = vi.fn().mockRejectedValueOnce(new Error("Try again"));
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog
        trigger={<button type="button">Connect channel</button>}
        title="Connect"
        fields={[{ name: "name", label: "Name", required: true }]}
        initialValues={{ name: "Initial" }}
        submitLabel="Connect"
        parseValues={parseRawValues}
        onSubmit={submit}
      />
    </AppRuntimeProvider>);

    const trigger = screen.getByRole("button", { name: "Connect channel" });
    fireEvent.click(trigger);
    expect(await screen.findByRole("dialog")).toBeTruthy();
    const input = screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Changed" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("Try again")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    fireEvent.click(trigger);
    expect((await screen.findByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe("Initial");
    expect(screen.queryByText("Try again")).toBeNull();
  });

  test("a dismissed trigger session ignores its pending submission", async () => {
    let resolve!: (value: FormSubmitResult<string>) => void;
    const onSubmitted = vi.fn();
    const submit = vi.fn(() => new Promise<FormSubmitResult<string>>((done) => { resolve = done; }));
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog
        trigger={<button type="button">Open request</button>}
        title="Request"
        fields={[]}
        submitLabel="Send"
        parseValues={parseRawValues}
        onSubmit={submit}
        onSubmitted={onSubmitted}
      />
    </AppRuntimeProvider>);

    const trigger = screen.getByRole("button", { name: "Open request" });
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(trigger);
    expect(await screen.findByRole("dialog")).toBeTruthy();
    resolve({ status: "ok", data: "old result" });
    await Promise.resolve();
    expect(onSubmitted).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  test("allows an optional-only dialog to submit its initial omitted value", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Optional" fields={[{ name: "input", label: "Input", widget: "json", omittable: true, nullable: true }]}
        submitLabel="Start" parseValues={(values) => values} onSubmit={submit} />
    </AppRuntimeProvider>);
    const button = screen.getByRole("button", { name: "Start" }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(submit).toHaveBeenCalledWith({}, { errorNotification: false }));
  });

  test("an untouched optional number is not seeded with the null its own type refuses: its parse sees no value", async () => {
    const parse = vi.fn(parseRawValues);
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Connect" fields={[
        { name: "host", label: "Host", required: true },
        { name: "port", label: "Port", kind: "integer" },
      ]} initialValues={{ host: "mail.example.com" }} submitLabel="Connect" parseValues={parse} onSubmit={submit} />
    </AppRuntimeProvider>);
    expect((screen.getByRole("textbox", { name: "Port" }) as HTMLInputElement).value).toBe("");
    const button = screen.getByRole("button", { name: "Connect" }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    // The registered control hands back `undefined`, no value, where it handed back `null`.
    expect(parse.mock.calls[0]![0]).toEqual({ host: "mail.example.com" });
    expect(parse.mock.calls[0]![0].port).toBeUndefined();
  });

  test("guard: an untouched required field keeps the submit disabled silently: a submit attempt shows no message and parses nothing", async () => {
    const parse = vi.fn(parseRawValues);
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Resize" fields={[
        { name: "size", label: "Size", kind: "integer", required: true },
      ]} submitLabel="Resize" parseValues={parse} onSubmit={submit} />
    </AppRuntimeProvider>);
    const button = screen.getByRole("button", { name: "Resize" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.submit(button.closest("form")!);
    // What is asserted is that nothing happens, so there is no effect to wait for: while the gate is
    // closed the dialog's submit handler returns before its check and starts nothing, so nothing can
    // arrive after this pause, however slow a save is. The pause (a task, not a microtask) only gives a
    // dialog that wrongly checked, or submitted, the time to show it.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(screen.queryByText("This field is required.")).toBeNull();
    expect(screen.getByRole("textbox", { name: "Size" }).getAttribute("aria-invalid")).toBeNull();
    expect(parse).not.toHaveBeenCalled();
    expect(submit).not.toHaveBeenCalled();
  });

  test("guard: a nullable field is seeded with null, not with its empty value", async () => {
    const seen = vi.fn((_values: Readonly<Record<string, unknown>>) => true);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Note" fields={[{ name: "note", label: "Note", nullable: true }]}
        submitLabel="Save" parseValues={parseRawValues} canSubmit={seen} onSubmit={vi.fn().mockResolvedValue(ok)} />
    </AppRuntimeProvider>);
    await waitFor(() => expect(seen).toHaveBeenCalled());
    expect(seen.mock.calls.at(-1)![0]).toStrictEqual({ note: null });
  });

  test("a date carried as text, holding null, offers no Set value: its declared format makes its empty value null", async () => {
    const parse = vi.fn(parseRawValues);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Plan" fields={deserializeFormSpec({ type: "object", properties: {
        due: { anyOf: [{ type: "string", format: "date", widget: "date" }, { type: "null" }], label: "Due" },
      } }, defaultWidgets)} submitLabel="Plan" parseValues={parse} onSubmit={vi.fn().mockResolvedValue(ok)} />
    </AppRuntimeProvider>);
    await screen.findByText("Left empty");
    expect(screen.queryByRole("button", { name: "Set value" })).toBeNull();
  });

  test("Add item on a list of dates carried as text keeps submit disabled, with the message under the item", async () => {
    const [days] = deserializeFormSpec({ type: "object", properties: {
      days: { type: "array", widget: "list", label: "Days", items: { type: "string", format: "date", widget: "date" } },
    } }, defaultWidgets);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Plan" fields={[days!]}
        submitLabel="Plan" parseValues={parseRawValues} onSubmit={vi.fn().mockResolvedValue(ok)} />
    </AppRuntimeProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Add item" }));
    const message = await screen.findByText("This field is required.");
    // The item's own row holds the message: the nearest row around it holds the item's control and
    // neither the list's label nor its "Add item".
    const row = message.closest('[data-layout="stack"]')!;
    expect(row.contains(screen.getByRole("button", { name: /^Item/ }))).toBe(true);
    expect(row.contains(screen.getByRole("button", { name: "Add item" }))).toBe(false);
    expect(row.contains(screen.getByText("Days"))).toBe(false);
    expect((screen.getByRole("button", { name: "Plan" }) as HTMLButtonElement).disabled).toBe(true);
  });

  test("a domain readiness gate blocks buttons and form submission until ready", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    const view = render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Plan" fields={[]}
        submitLabel="Start" parseValues={(values) => values} onSubmit={submit}
        canSubmit={() => false} />
    </AppRuntimeProvider>);
    const button = screen.getByRole("button", { name: "Start" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.submit(button.closest("form")!);
    expect(submit).not.toHaveBeenCalled();

    view.rerender(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Plan" fields={[]}
        submitLabel="Start" parseValues={(values) => values} onSubmit={submit}
        canSubmit={() => true} />
    </AppRuntimeProvider>);
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.submit(button.closest("form")!);
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
  });

  test("a transport failure permits retry without changing valid dialog values", async () => {
    const submit = vi.fn().mockRejectedValueOnce(new Error("Try again")).mockResolvedValueOnce(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Connect" fields={[{ name: "name", label: "Name", required: true }]}
        initialValues={{ name: "Ada" }} submitLabel="Connect" parseValues={(values) => values} onSubmit={submit} />
    </AppRuntimeProvider>);
    const button = () => screen.getByRole("button", { name: "Connect" }) as HTMLButtonElement;
    await waitFor(() => expect(button().disabled).toBe(false));
    fireEvent.click(button());
    await waitFor(() => expect(screen.getByText("Try again")).toBeTruthy());
    await waitFor(() => expect(button().disabled).toBe(false));
    fireEvent.click(button());
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
  });

  test("an invalid submit result keeps field issues and values until corrected", async () => {
    const submit = vi.fn().mockResolvedValueOnce({
      status: "invalid", issues: { fieldErrors: { name: ["Choose a different name"] }, formErrors: [] },
    }).mockResolvedValueOnce({ status: "ok", data: "saved" });
    const submitted = vi.fn();
    const close = vi.fn();
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={close} title="Edit" fields={[{ name: "name", label: "Name", required: true }]}
        initialValues={{ name: "Initial" }} submitLabel="Save" parseValues={parseRawValues}
        onSubmit={submit} onSubmitted={submitted} />
    </AppRuntimeProvider>);
    const button = () => screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    await waitFor(() => expect(button().disabled).toBe(false));
    fireEvent.click(button());
    expect(await screen.findByText("Choose a different name")).toBeTruthy();
    expect(submitted).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
    const input = screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement;
    expect(input.value).toBe("Initial");
    fireEvent.change(input, { target: { value: "Corrected" } });
    await waitFor(() => expect(button().disabled).toBe(false));
    fireEvent.click(button());
    await waitFor(() => expect(submitted).toHaveBeenCalledWith("saved", { name: "Corrected" }));
    expect(close).toHaveBeenCalledWith(false);
  });

  test("a conflict stays open and permits retrying unchanged values", async () => {
    const submit = vi.fn().mockResolvedValueOnce({ status: "conflict", message: "The entry changed" })
      .mockResolvedValueOnce({ status: "ok", data: "saved" });
    const submitted = vi.fn();
    const close = vi.fn();
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={close} title="Edit" fields={[{ name: "name", label: "Name", required: true }]}
        initialValues={{ name: "Initial" }} submitLabel="Save" parseValues={parseRawValues}
        onSubmit={submit} onSubmitted={submitted} />
    </AppRuntimeProvider>);
    const button = () => screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    await waitFor(() => expect(button().disabled).toBe(false));
    fireEvent.click(button());
    expect(await screen.findByText("The entry changed")).toBeTruthy();
    expect(submitted).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
    await waitFor(() => expect(button().disabled).toBe(false));
    fireEvent.click(button());
    await waitFor(() => expect(submitted).toHaveBeenCalledWith("saved", { name: "Initial" }));
    expect(close).toHaveBeenCalledWith(false);
  });

  test("issues outside rendered controls permit retrying unchanged values", async () => {
    const submit = vi.fn().mockResolvedValueOnce({ status: "invalid", issues: {
      fieldErrors: { missing: ["The entry changed"], token: ["Refresh the entry"] }, formErrors: [],
    } }).mockResolvedValueOnce({ status: "ok", data: "saved" });
    const submitted = vi.fn();
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Edit" fields={[
        { name: "name", label: "Name", required: true }, { name: "token", hidden: true },
      ]} initialValues={{ name: "Initial", token: "retained" }} submitLabel="Save" parseValues={parseRawValues}
        onSubmit={submit} onSubmitted={submitted} />
    </AppRuntimeProvider>);
    const button = () => screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    await waitFor(() => expect(button().disabled).toBe(false));
    fireEvent.click(button());
    expect(await screen.findByText(/The entry changed/)).toBeTruthy();
    expect(screen.getByText(/Refresh the entry/)).toBeTruthy();
    expect(submitted).not.toHaveBeenCalled();
    await waitFor(() => expect(button().disabled).toBe(false));
    fireEvent.click(button());
    await waitFor(() => expect(submitted).toHaveBeenCalledWith("saved", { name: "Initial", token: "retained" }));
  });

  test("binds validation issues to nested fields and keeps unmatched issues visible", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    const fields = deserializeFormSpec({ type: "object", properties: {
      details: { type: "object", widget: "object", properties: {
        title: { type: "string", label: "Title" },
      } },
    } }, defaultWidgets);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Edit details" fields={[...fields, { name: "token", hidden: true }]}
        initialValues={{ details: { title: "Initial" } }} submitLabel="Save"
        parseValues={parseRawValues} onSubmit={submit}
        validate={() => ({
          fieldErrors: {
            "details.title": ["Choose a title", "Use a unique value"],
            missing: ["The entry changed"],
            token: ["Refresh the entry"],
          },
          formErrors: ["Review the values"],
        })} />
    </AppRuntimeProvider>);

    await screen.findByRole("textbox", { name: "Title" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const message = await screen.findByText("Choose a title Use a unique value");
    const input = screen.getByRole("textbox", { name: "Title" });
    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(message.id);
    // The form-level message and each message no rendered field claims are separate items of the banner.
    const banner = screen.getByText("Review the values").closest<HTMLElement>('[role="alert"]')!;
    expect(within(banner).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Review the values", "missing: The entry changed", "token: Refresh the entry",
    ]);
    expect(submit).not.toHaveBeenCalled();
  });

  test("a dismissed session ignores pending validation before firing the mutation", async () => {
    let finish!: (value: null) => void;
    const submit = vi.fn().mockResolvedValue(ok);
    const validate = vi.fn(() => new Promise<null>((resolve) => { finish = resolve; }));
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog trigger={<button type="button">Open request</button>} title="Request"
        fields={[]} submitLabel="Send" parseValues={parseRawValues} onSubmit={submit} validate={validate} />
    </AppRuntimeProvider>);

    fireEvent.click(screen.getByRole("button", { name: "Open request" }));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(validate).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    finish(null);
    await Promise.resolve();
    expect(submit).not.toHaveBeenCalled();
  });

  test("resolves dependent descriptor options from current values without replacing an unknown value", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog
        open
        onOpenChange={vi.fn()}
        title="Connect"
        fields={[
          { name: "source", label: "Source", widget: "select", options: [{ value: "a", label: "A" }, { value: "b", label: "B" }] },
          {
            name: "outcome",
            label: "Outcome",
            resolve: (values) => ({
              widget: "select",
              options: [
                { value: "", label: "Any outcome" },
                { value: values.source === "a" ? "completed" : "pending", label: values.source === "a" ? "Completed" : "Pending" },
                { value: String(values.outcome), label: `Unavailable (${String(values.outcome)})` },
              ],
            }),
          },
        ]}
        initialValues={{ source: "a", outcome: "legacy" }}
        submitLabel="Connect"
        parseValues={parseRawValues}
        onSubmit={submit}
      />
    </AppRuntimeProvider>);

    fireEvent.click(screen.getByRole("combobox", { name: "Source" }));
    const sourceB = screen.getByRole("option", { name: "B" });
    fireEvent.pointerDown(sourceB, { pointerType: "mouse" });
    fireEvent.click(sourceB);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Source" }).textContent).toContain("B"));
    fireEvent.click(screen.getByRole("combobox", { name: "Outcome" }));
    expect(await screen.findByRole("option", { name: "Pending" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Unavailable (legacy)" })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Outcome" }), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() => expect(submit).toHaveBeenCalledWith({ source: "b", outcome: "legacy" }, { errorNotification: false }));
  });

  test("revalidates resolved required and read-only state while retaining declared field identity", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog
        open
        onOpenChange={vi.fn()}
        title="Dependent"
        fields={[
          { name: "mode", label: "Mode", widget: "select", options: [{ value: "fixed", label: "Fixed" }, { value: "custom", label: "Custom" }] },
          {
            name: "value",
            label: "Value",
            // This cast hides the returned `name` from the compiler; the declared name must still win.
            resolve: (values) => ({
              name: "wrong-name",
              required: values.mode === "custom",
              readOnly: values.mode !== "custom",
            }) as Omit<DescriptorField, "name">,
          },
        ]}
        initialValues={{ mode: "fixed", value: "" }}
        submitLabel="Save"
        parseValues={parseRawValues}
        onSubmit={submit}
      />
    </AppRuntimeProvider>);

    expect(screen.queryByRole("textbox", { name: "Value" })).toBeNull();
    fireEvent.click(screen.getByRole("combobox", { name: "Mode" }));
    const custom = screen.getByRole("option", { name: "Custom" });
    fireEvent.pointerDown(custom, { pointerType: "mouse" });
    fireEvent.click(custom);
    const value = await screen.findByRole("textbox", { name: "Value" }) as HTMLInputElement;
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(value, { target: { value: "kept" } });
    await waitFor(() => expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ mode: "custom", value: "kept" }, { errorNotification: false }));
  });

  test("a required nullable FormSpec value accepts explicit null", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Nullable" fields={[
        { name: "note", label: "Note", required: true, nullable: true, presenceRequired: true },
      ]} initialValues={{ note: null }} submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);

    const button = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ note: null }, { errorNotification: false }));
  });

  test("each value rule shows its own message, not that the field is required", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    const fields = deserializeFormSpec({ type: "object", properties: {
      handle: { type: "string", label: "Handle", minLength: 3 },
      code: { type: "string", label: "Code", maxLength: 4 },
      low: { type: "integer", label: "Low", minimum: 5 },
      high: { type: "integer", label: "High", maximum: 10 },
      count: { type: "integer", label: "Count", omittable: true },
      tags: { type: "array", widget: "list", label: "Tags", minItems: 2, items: { type: "string" } },
      limits: { type: "array", widget: "list", label: "Limits", maxItems: 1, items: { type: "string" } },
    } }, defaultWidgets);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Rules" fields={fields}
        initialValues={{ handle: "ab", code: "abcde", low: 2, high: 11, count: "", tags: ["a"], limits: ["a", "b"] }}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Enter at least 3 characters.");
    const messageUnder = (label: string) => screen.getByText(label).closest('[data-layout="stack"]')?.textContent;
    for (const [label, message] of Object.entries({
      Handle: "Enter at least 3 characters.",
      Code: "Enter no more than 4 characters.",
      Low: "Enter a value of at least 5.",
      High: "Enter a value of at most 10.",
      Count: "Enter a valid whole number.",
      Tags: "Add at least 2 items.",
      Limits: "Keep no more than 1 item.",
    })) {
      expect(messageUnder(label)).toContain(message);
    }
    expect(screen.queryByText("This field is required.")).toBeNull();
    expect(submit).not.toHaveBeenCalled();
  });

  test("an empty text under a minimum length of one is required unless the field is omittable, and keeps the length message under a minimum above one", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    const fields = deserializeFormSpec({ type: "object", properties: {
      slug: { type: "string", label: "Slug", minLength: 1, maxLength: 64 },
      nick: { type: "string", label: "Nick", minLength: 1, omittable: true },
      handle: { type: "string", label: "Handle", minLength: 3 },
    } }, defaultWidgets);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Texts" fields={fields} initialValues={{ slug: "", nick: "", handle: "" }}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Enter at least 3 characters.");
    const messageUnder = (label: string) => screen.getByText(label).closest('[data-layout="stack"]')?.textContent;
    expect(messageUnder("Slug")).toContain("This field is required.");
    expect(messageUnder("Nick")).toContain("Enter at least 1 character.");
    expect(messageUnder("Nick")).not.toContain("This field is required.");
    expect(messageUnder("Handle")).not.toContain("This field is required.");
    expect(submit).not.toHaveBeenCalled();
  });

  test("a cleared nullable text under a minimum length of one keeps the length message, and a cleared required one says the field is required", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    const fields = deserializeFormSpec({ type: "object", properties: {
      caption: { type: "string", label: "Caption", minLength: 1, nullable: true },
      slug: { type: "string", label: "Slug", minLength: 1 },
    } }, defaultWidgets);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Texts" fields={fields} initialValues={{ caption: "hi", slug: "front-desk" }}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);

    fireEvent.change(screen.getByRole("textbox", { name: "Caption" }), { target: { value: "" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Slug" }), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("Enter at least 1 character.");
    const messageUnder = (label: string) => screen.getByText(label).closest('[data-layout="stack"]')?.textContent;
    expect(messageUnder("Caption")).toContain("Enter at least 1 character.");
    expect(messageUnder("Caption")).not.toContain("This field is required.");
    expect(messageUnder("Slug")).toContain("This field is required.");
    expect(submit).not.toHaveBeenCalled();
  });

  test("hidden required fields do not block the editable dialog", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Edit" fields={[
        { name: "name", label: "Name", required: true },
        { name: "token", required: true, hidden: true },
      ]} submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);

    const button = screen.getByRole<HTMLButtonElement>("button", { name: "Save" });
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), { target: { value: "Ready" } });
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ name: "Ready", token: "" }, { errorNotification: false }));
  });

  test("keeps submit invalid for a missing nested required list constraint", async () => {
    const [slots] = deserializeFormSpec({
      type: "object", required: ["slots"], properties: {
        slots: { type: "array", widget: "list", presenceRequired: true, minItems: 1, items: {
          type: "object", widget: "object", required: ["assignees"], properties: {
            assignees: { type: "array", widget: "list", presenceRequired: true, minItems: 1, items: { type: "string" } },
          },
        } },
      },
    }, defaultWidgets);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Slots" fields={[slots!]}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={vi.fn().mockResolvedValue(ok)} />
    </AppRuntimeProvider>);

    fireEvent.click(await screen.findByRole("button", { name: "Add item" }));
    expect(await screen.findByText("This field is required.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  test("associates descriptor labels and descriptions with widget inputs", () => {
    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <MutationDialog
          open
          onOpenChange={vi.fn()}
          title="Connect Telegram"
          fields={[
            {
              name: "api_hash",
              label: "API hash",
              widget: "password",
              description: "Create or copy your Telegram application keys.",
            },
          ]}
          submitLabel="Connect"
          parseValues={parseRawValues}
          onSubmit={vi.fn().mockResolvedValue(ok)}
        />
      </AppRuntimeProvider>,
    );

    const label = screen.getByText("API hash").closest("label");
    const input = screen.getByLabelText("API hash");
    const description = screen.getByText(
      "Create or copy your Telegram application keys.",
    );

    expect(label?.htmlFor).toBe(input.id);
    expect(input.id).not.toBe("");
    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(
      description.id,
    );
  });

  test("binds server messages to the matching shared field control", () => {
    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <LabeledDescriptorField
          field={{ name: "title", label: "Title" }}
          value=""
          messages={["This field is required."]}
          onChange={vi.fn()}
        />
      </AppRuntimeProvider>,
    );

    const input = screen.getByLabelText("Title");
    const message = screen.getByText("This field is required.");

    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(
      message.id,
    );
    expect(message.closest('[data-invalid=""]')).not.toBeNull();
  });

  test("an unknown relation degrades to a disabled control and development warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    render(
      <Provider resources={[testDataResource("parties.Party")]}>
        <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <MutationDialog
            open
            onOpenChange={vi.fn()}
            title="Assign owner"
            fields={[
              {
                name: "owner",
                label: "Owner",
                relation: {
                  resource: "missing.Person",
                  labelField: "display_name",
                },
              },
            ]}
            submitLabel="Assign"
            parseValues={parseRawValues}
            onSubmit={vi.fn().mockResolvedValue(ok)}
          />
        </AppRuntimeProvider>
      </Provider>,
    );

    expect(
      (screen.getByRole("combobox", { name: "Owner" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(warn).toHaveBeenCalledWith(
      expect.stringMatching(/descriptor relation.*missing\.Person/),
    );
    warn.mockRestore();
  });

  test("decodes raw controls before submitting typed values", async () => {
    const onSubmit = vi.fn().mockResolvedValue(ok);
    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <MutationDialog
          open
          onOpenChange={vi.fn()}
          title="Create"
          fields={[
            { name: "name", label: "Name", required: true },
            { name: "note", label: "Note" },
          ]}
          submitLabel="Create"
          parseValues={(values) => ({
            name: mutationDialogValueCodecs.requiredString(values.name, "name"),
            note: mutationDialogValueCodecs.string(values.note),
          })}
          onSubmit={onSubmit}
        />
      </AppRuntimeProvider>,
    );

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "  Ada  " },
    });
    fireEvent.change(screen.getByLabelText("Note"), {
      target: { value: "   " },
    });
    await waitFor(() => expect((screen.getByRole("button", { name: "Create" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({ name: "Ada", note: null }, { errorNotification: false }),
    );
  });

  test("keeps whitespace only through the explicit verbatim-string codec", () => {
    expect(mutationDialogValueCodecs.string("  secret  ")).toBe("secret");
    expect(mutationDialogValueCodecs.string("   ")).toBeNull();
    expect(mutationDialogValueCodecs.string({ value: "secret" })).toBeNull();
    expect(() =>
      mutationDialogValueCodecs.requiredString({ value: "secret" }, "name"),
    ).toThrow('MutationDialog invariant: required field "name"');
    expect(
      mutationDialogValueCodecs.integer(
        " 4 ",
        "Count",
        (label) => `${label} must be a whole number.`,
      ),
    ).toBe(4);
    expect(mutationDialogValueCodecs.verbatimString("  secret  ", "secret")).toBe(
      "  secret  ",
    );
    expect(() => mutationDialogValueCodecs.verbatimString("", "secret")).toThrow(
      'MutationDialog invariant: required verbatim field "secret"',
    );
    expect(() =>
      mutationDialogValueCodecs.integer(
        "4.5",
        "Port",
        (label) => `${label} must be a whole number.`,
      ),
    ).toThrow("Port must be a whole number.");

    const localMidnight = mutationDialogValueCodecs.datetime(
      "2026-08-31T00:00",
    );
    expect(localMidnight).toMatch(
      /^2026-08-31T00:00:00[+-]\d{2}:\d{2}$/,
    );
    expect(mutationDialogValueCodecs.datetime("")).toBeNull();
    expect(() => mutationDialogValueCodecs.datetime("not-a-date")).toThrow(
      "datetime value was not a valid ISO-8601 date-time",
    );
  });

  test("shows owner-level busy feedback and locks both footer actions", async () => {
    let finishSubmit: (() => void) | undefined;
    const pendingSubmit = new Promise<void>((resolve) => {
      finishSubmit = resolve;
    });
    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <MutationDialog
          open
          onOpenChange={vi.fn()}
          title="Connect"
          fields={[{ name: "name", label: "Name", required: true }]}
          submitLabel="Connect"
          submittingLabel="Connecting…"
          parseValues={parseRawValues}
          onSubmit={async () => { await pendingSubmit; return ok; }}
        />
      </AppRuntimeProvider>,
    );

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Ada" },
    });
    await waitFor(() => expect((screen.getByRole("button", { name: "Connect" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: "Connecting…" })
          .getAttribute("aria-busy"),
      ).toBe("true"),
    );
    expect(
      (screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    finishSubmit?.();
    await pendingSubmit;
  });
});

test("the dialog submission owner displays the successful result's message once", async () => {
  const onSubmitted = vi.fn();
  render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <MutationDialog open onOpenChange={() => undefined} title="Send request" fields={[]}
      submitLabel="Send" parseValues={parseRawValues} closeOnSubmit={false}
      onSubmit={() => ({ status: "ok", data: "accepted", message: "Request accepted" })}
      onSubmitted={onSubmitted} />
  </AppRuntimeProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findAllByText("Request accepted")).toHaveLength(1);
  expect(onSubmitted).toHaveBeenCalledExactlyOnceWith("accepted", {});
});

test("a refusal bound to a field is reported once: under the field, with no banner and no toast", async () => {
  const refusal = { graphQLErrors: [{ message: "The calendar feed address is not allowed.", extensions: {
    code: "VALIDATION", validationErrors: { url: ["The calendar feed address is not allowed."] },
  } }] };
  const mutation = vi.fn().mockRejectedValue(refusal);
  render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <MutationDialog open onOpenChange={() => undefined} title="Subscribe" fields={[{ name: "url", label: "Feed address", required: true }]}
      initialValues={{ url: "webcal://127.0.0.1/feed.ics" }} submitLabel="Subscribe" errorFallback="Could not subscribe to the calendar feed."
      parseValues={parseRawValues} mutation={mutation} />
  </AppRuntimeProvider>);
  const button = () => screen.getByRole("button", { name: "Subscribe" }) as HTMLButtonElement;
  await waitFor(() => expect(button().disabled).toBe(false));
  fireEvent.click(button());

  expect(await screen.findAllByText("The calendar feed address is not allowed.")).toHaveLength(1);
  expect(screen.getByRole("textbox", { name: /Feed address/ }).getAttribute("aria-invalid")).toBe("true");
  expect(screen.queryByText("Could not subscribe to the calendar feed.")).toBeNull();
  // The dialog runs the call with refine's generic error notification off: no toast repeats it.
  expect(mutation).toHaveBeenCalledExactlyOnceWith({ url: "webcal://127.0.0.1/feed.ics" }, { errorNotification: false });
});

test("validate and onSubmit get the call options that keep refine from toasting what the dialog renders", async () => {
  const transport = new Error("The daemon is unreachable.");
  const validate = vi.fn().mockResolvedValue(null);
  const onSubmit = vi.fn().mockRejectedValue(transport);
  render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <MutationDialog open onOpenChange={() => undefined} title="Create workspace" fields={[]} submitLabel="Create"
      errorFallback="Could not create the workspace." parseValues={parseRawValues}
      validate={validate} onSubmit={onSubmit} />
  </AppRuntimeProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Create" }));

  // The dialog renders the failure; each call it makes for its caller runs quiet, so no toast repeats it.
  expect(await screen.findAllByText("The daemon is unreachable.")).toHaveLength(1);
  expect(validate).toHaveBeenCalledExactlyOnceWith({}, { errorNotification: false });
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith({}, { errorNotification: false });
});

test("a mutation's data acknowledges the dialog; missing data fails with the dialog's fallback", async () => {
  const onSubmitted = vi.fn();
  const mutation = vi.fn().mockResolvedValueOnce(undefined).mockResolvedValueOnce({ subscribe: { id: "src_1" } });
  render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <MutationDialog open onOpenChange={() => undefined} title="Subscribe" fields={[]} submitLabel="Subscribe"
      errorFallback="Could not subscribe." parseValues={parseRawValues} closeOnSubmit={false}
      mutation={mutation} mutationResult={(data: { subscribe: { id: string } }) => ({ status: "ok", data: data.subscribe.id })}
      onSubmitted={onSubmitted} />
  </AppRuntimeProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Subscribe" }));
  expect(await screen.findByText("Could not subscribe.")).toBeTruthy();
  await waitFor(() => expect((screen.getByRole("button", { name: "Subscribe" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Subscribe" }));
  await waitFor(() => expect(onSubmitted).toHaveBeenCalledExactlyOnceWith("src_1", {}));
});

test("a submission settles its promise whatever its call does, a synchronous throw included", async () => {
  const refusal = new Error("Refused before any request.");
  const mutation = vi.fn((): Promise<{ id: string } | undefined> => { throw refusal; });
  await expect(submitMutationDialog({ mutation }, {}, "Could not save.")).rejects.toBe(refusal);
  const onSubmit = vi.fn((): FormSubmitResult<string> => { throw refusal; });
  await expect(submitMutationDialog({ onSubmit }, {}, "Could not save.")).rejects.toBe(refusal);
  expect(mutation).toHaveBeenCalledExactlyOnceWith({}, { errorNotification: false });
});

const conditionalFields = [
  { name: "mode", label: "Mode" },
  { name: "detail", label: "Detail", showWhen: (values: Readonly<Record<string, unknown>>) => values.mode === "on" },
];

test("a field hidden when the dialog opens is not submitted, and shows its initial value once it shows", async () => {
  const submit = vi.fn().mockResolvedValue(ok);
  render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <MutationDialog open onOpenChange={vi.fn()} title="Conditional" fields={conditionalFields}
      initialValues={{ mode: "off", detail: "seeded" }} submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
  </AppRuntimeProvider>);
  expect(screen.queryByRole("textbox", { name: "Detail" })).toBeNull();
  // The hidden field keeps its default without a value, so the form reads dirty and is ready once it has validated.
  const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
  await waitFor(() => expect(save.disabled).toBe(false));
  fireEvent.click(save);
  await waitFor(() => expect(submit).toHaveBeenCalledWith({ mode: "off" }, { errorNotification: false }));
  fireEvent.change(screen.getByRole("textbox", { name: "Mode" }), { target: { value: "on" } });
  expect((screen.getByRole("textbox", { name: "Detail" }) as HTMLInputElement).value).toBe("seeded");
});

test("a field that shows again has its value before its control renders: the form is not updated during a render", async () => {
  const errors: string[] = [];
  const consoleError = vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => { errors.push(String(args[0])); });
  try {
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Conditional" fields={conditionalFields}
        initialValues={{ mode: "on", detail: "initial" }} submitLabel="Save" parseValues={parseRawValues} onSubmit={vi.fn().mockResolvedValue(ok)} />
    </AppRuntimeProvider>);
    const mode = screen.getByRole("textbox", { name: "Mode" });
    fireEvent.change(mode, { target: { value: "off" } });
    expect(screen.queryByRole("textbox", { name: "Detail" })).toBeNull();
    fireEvent.change(mode, { target: { value: "on" } });
    expect((screen.getByRole("textbox", { name: "Detail" }) as HTMLInputElement).value).toBe("initial");
    expect(errors.filter((message) => message.includes("Cannot update a component"))).toEqual([]);
  } finally {
    consoleError.mockRestore();
  }
});

describe("an object field's member messages", () => {
  // An object field beside a list, as a FormSpec gives them: each member and list item has its own rule.
  const memberFields = deserializeFormSpec({ type: "object", properties: {
    retry: { type: "object", widget: "object", label: "Retry", properties: {
      handle: { type: "string", label: "Handle", minLength: 3 },
      delay: { type: "integer", label: "Delay", maximum: 10 },
      backoff: { type: "object", widget: "object", label: "Backoff", properties: {
        factor: { type: "integer", label: "Factor", minimum: 2 },
      } },
    } },
    tags: { type: "array", widget: "list", label: "Tags", items: { type: "string", label: "Tag", maxLength: 1 } },
  } }, defaultWidgets);
  const valid = { retry: { handle: "abc", delay: 5, backoff: { factor: 2 } }, tags: [] };
  const handleRule = "Enter at least 3 characters.";
  const delayRule = "Enter a value of at most 10.";

  async function renderMembers(initialValues: Record<string, unknown>) {
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Retry" fields={memberFields} initialValues={initialValues}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);
    // The object widget's members mount after the dialog: wait for one before acting on them.
    await screen.findByLabelText("Handle");
    const save = screen.getByRole<HTMLButtonElement>("button", { name: "Save" });
    return { submit, save, form: save.closest("form")! };
  }
  const input = (label: string) => screen.getByLabelText(label) as HTMLInputElement;
  const messageUnder = (label: string) => screen.getByText(label).closest('[data-layout="stack"]')?.textContent ?? "";
  // The change's validation runs after the event; a macrotask lets it settle before asserting what stays.
  const settle = () => act(() => new Promise((resolve) => setTimeout(resolve, 20)));

  test("a failing value typed into a member shows its message before any submit; Save is disabled only while a message shows", async () => {
    const { submit, save, form } = await renderMembers(valid);
    await waitFor(() => expect(save.disabled).toBe(false));

    fireEvent.change(input("Handle"), { target: { value: "ab" } });
    expect(await screen.findByText(handleRule)).toBeTruthy();
    expect(messageUnder("Handle")).toContain(handleRule);
    expect(input("Handle").getAttribute("aria-invalid")).toBe("true");
    expect(save.disabled).toBe(true);
    fireEvent.submit(form);
    await settle();
    expect(submit).not.toHaveBeenCalled();

    fireEvent.change(input("Handle"), { target: { value: "abcd" } });
    await waitFor(() => expect(screen.queryByText(handleRule)).toBeNull());
    expect(input("Handle").getAttribute("aria-invalid")).toBeNull();
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(submit).toHaveBeenCalledExactlyOnceWith(
      { ...valid, retry: { ...valid.retry, handle: "abcd" } }, { errorNotification: false },
    ));
  });

  test("after a blocked submit, an edit that still fails keeps the member's message, and correcting it removes the message and enables Save", async () => {
    const { submit, save, form } = await renderMembers({ ...valid, retry: { ...valid.retry, handle: "ab" } });
    fireEvent.submit(form);
    expect(await screen.findByText(handleRule)).toBeTruthy();
    expect(submit).not.toHaveBeenCalled();

    fireEvent.change(input("Handle"), { target: { value: "a" } });
    await settle();
    expect(messageUnder("Handle")).toContain(handleRule);
    expect(input("Handle").getAttribute("aria-invalid")).toBe("true");
    expect(save.disabled).toBe(true);

    fireEvent.change(input("Handle"), { target: { value: "abcd" } });
    await waitFor(() => expect(screen.queryByText(handleRule)).toBeNull());
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]![0]).toStrictEqual({ ...valid, retry: { ...valid.retry, handle: "abcd" } });
  });

  test("guard: a list item's message stays through an edit that still fails and goes when it is corrected", async () => {
    const { submit, form } = await renderMembers({ ...valid, tags: ["xy"] });
    fireEvent.submit(form);
    const itemRule = "Enter no more than 1 character.";
    expect(await screen.findByText(itemRule)).toBeTruthy();

    fireEvent.change(input("Tag"), { target: { value: "xyz" } });
    await settle();
    expect(messageUnder("Tag")).toContain(itemRule);
    expect(input("Tag").getAttribute("aria-invalid")).toBe("true");

    fireEvent.change(input("Tag"), { target: { value: "x" } });
    await waitFor(() => expect(screen.queryByText(itemRule)).toBeNull());
    expect(submit).not.toHaveBeenCalled();
  });

  test("of two members failing at once, correcting one removes only that member's message", async () => {
    const { submit, save } = await renderMembers(valid);
    fireEvent.change(input("Handle"), { target: { value: "ab" } });
    fireEvent.change(input("Delay"), { target: { value: "11" } });
    expect(await screen.findByText(delayRule)).toBeTruthy();
    expect(messageUnder("Handle")).toContain(handleRule);

    fireEvent.change(input("Handle"), { target: { value: "abcd" } });
    await waitFor(() => expect(screen.queryByText(handleRule)).toBeNull());
    expect(messageUnder("Delay")).toContain(delayRule);
    expect(input("Delay").getAttribute("aria-invalid")).toBe("true");
    expect(save.disabled).toBe(true);
    expect(submit).not.toHaveBeenCalled();
  });

  test("a member of an object inside the object shows its message, which stays through an edit that still fails", async () => {
    const { submit, save } = await renderMembers(valid);
    const factorRule = "Enter a value of at least 2.";
    fireEvent.change(input("Factor"), { target: { value: "1" } });
    expect(await screen.findByText(factorRule)).toBeTruthy();
    expect(messageUnder("Factor")).toContain(factorRule);

    fireEvent.change(input("Factor"), { target: { value: "0" } });
    await settle();
    expect(messageUnder("Factor")).toContain(factorRule);
    expect(input("Factor").getAttribute("aria-invalid")).toBe("true");
    // The object fields around it show no message of their own.
    expect(screen.getAllByText(factorRule)).toHaveLength(1);
    expect(save.disabled).toBe(true);
    expect(submit).not.toHaveBeenCalled();
  });

  // An object field whose member is named as one of React Hook Form's own attributes of an error.
  async function renderNamedMember(
    member: "type" | "ref", initialValues: Record<string, unknown>, result: FormSubmitResult<unknown> = ok,
  ) {
    const fields = deserializeFormSpec({ type: "object", properties: {
      retry: { type: "object", widget: "object", label: "Retry", properties: {
        [member]: { type: "string", label: "Kind", minLength: 3 },
        handle: { type: "string", label: "Handle", minLength: 3 },
      } },
    } }, defaultWidgets);
    const submit = vi.fn().mockResolvedValue(result);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Retry" fields={fields} initialValues={initialValues}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);
    await screen.findByLabelText("Kind");
    const save = screen.getByRole<HTMLButtonElement>("button", { name: "Save" });
    return { submit, save, form: save.closest("form")! };
  }

  async function namedMemberKeepsItsMessage(member: "type" | "ref") {
    const { submit, save, form } = await renderNamedMember(member, { retry: { [member]: "abc", handle: "abc" } });
    await waitFor(() => expect(save.disabled).toBe(false));

    fireEvent.change(input("Kind"), { target: { value: "ab" } });
    expect(await screen.findByText(handleRule)).toBeTruthy();
    expect(messageUnder("Kind")).toContain(handleRule);
    expect(input("Kind").getAttribute("aria-invalid")).toBe("true");
    expect(save.disabled).toBe(true);
    fireEvent.submit(form);
    await settle();
    expect(submit).not.toHaveBeenCalled();

    fireEvent.change(input("Kind"), { target: { value: "a" } });
    await settle();
    expect(messageUnder("Kind")).toContain(handleRule);
    expect(input("Kind").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getAllByText(handleRule)).toHaveLength(1);
    expect(save.disabled).toBe(true);

    fireEvent.change(input("Kind"), { target: { value: "abcd" } });
    await waitFor(() => expect(screen.queryByText(handleRule)).toBeNull());
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]![0]).toStrictEqual({ retry: { [member]: "abcd", handle: "abc" } });
  }

  test("a member named `type` shows its message as any member does: a failing value shows it with Save disabled and nothing submitted, an edit that still fails keeps it, and correcting it removes it and enables Save", async () => {
    await namedMemberKeepsItsMessage("type");
  });

  test.fails("defect: a member named `ref` shows no message, since the message walk never walks `ref`, where React Hook Form keeps the registered control; it should show its message as any member does", async () => {
    await namedMemberKeepsItsMessage("ref");
  });

  test("guard: a member named `type` holding a valid value beside a failing sibling leaves the sibling's message under the sibling, and the marker of the field's node never shows", async () => {
    const { submit, save, form } = await renderNamedMember("type", { retry: { type: "abc", handle: "abc" } });
    fireEvent.change(input("Handle"), { target: { value: "ab" } });
    expect(await screen.findByText(handleRule)).toBeTruthy();
    expect(messageUnder("Handle")).toContain(handleRule);
    expect(messageUnder("Kind")).not.toContain(handleRule);
    expect(input("Kind").getAttribute("aria-invalid")).toBeNull();
    expect(screen.queryByText(new RegExp(MEMBER_ERRORS))).toBeNull();
    expect(save.disabled).toBe(true);
    fireEvent.submit(form);
    await settle();
    expect(submit).not.toHaveBeenCalled();
  });

  // The server refuses the object field and its member named `type` at once, the field's own error
  // holding React Hook Form's attribute `type: "server"` at the key where the member's error sits.
  async function refuseFieldAndTypeMember(fieldErrors: Record<string, readonly string[]>) {
    const { submit, save } = await renderNamedMember("type", { retry: { type: "abc", handle: "abc" } },
      { status: "invalid", issues: { fieldErrors, formErrors: [] } });
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    const fieldMessage = await screen.findByText(/^Bad retry\./);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    const kind = input("Kind").getAttribute("aria-describedby")?.split(" ").filter(Boolean)
      .map((id) => document.getElementById(id)?.textContent) ?? [];
    return { field: fieldMessage.textContent, kind };
  }

  test("a server refusal of an object field, then of its member named `type`: the field shows exactly its own message and the member exactly its own", async () => {
    const shown = await refuseFieldAndTypeMember({ retry: ["Bad retry."], "retry.type": ["Bad kind."] });
    expect(shown).toStrictEqual({ field: "Bad retry.", kind: ["Bad kind."] });
    expect(screen.getAllByText(/Bad retry\./)).toHaveLength(1);
  });

  test("guard: a server refusal of the member named `type`, then of its object field: the field shows exactly its own message", async () => {
    const shown = await refuseFieldAndTypeMember({ "retry.type": ["Bad kind."], retry: ["Bad retry."] });
    expect(shown.field).toBe("Bad retry.");
    expect(screen.getAllByText(/Bad retry\./)).toHaveLength(1);
  });

  test.fails("defect: a server refusal of the member named `type`, then of its object field, loses the member's message, since React Hook Form's setError gives the field's own `type` the key where the member's error sat; it should show it under the member", async () => {
    const shown = await refuseFieldAndTypeMember({ "retry.type": ["Bad kind."], retry: ["Bad retry."] });
    expect(shown.kind).toStrictEqual(["Bad kind."]);
  });
});

test("guard: a server refusal of a registered top-level field shows exactly its message under it, whether its control attaches React Hook Form's ref or not", async () => {
  const submit = vi.fn().mockResolvedValue({
    status: "invalid", issues: { fieldErrors: { code: ["Taken."], source: ["Unknown source."] }, formErrors: [] },
  });
  render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <MutationDialog open onOpenChange={vi.fn()} title="Register a code" initialValues={{ code: "abc", source: "x" }}
      submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} fields={[
        { name: "code", label: "Code" },
        // A control of its own that never attaches the control ref it is given.
        { name: "source", label: "Source", control: ({ id, value, describedBy, invalid, onChange }) => (
          <input id={id} aria-describedby={describedBy} aria-invalid={invalid || undefined}
            value={typeof value === "string" ? value : ""} onChange={(event) => onChange(event.target.value)} />
        ) },
      ]} />
  </AppRuntimeProvider>);
  const code = await screen.findByLabelText("Code");
  const source = screen.getByLabelText("Source");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Taken.")).toBeTruthy();
  const describedText = (control: HTMLElement) => (control.getAttribute("aria-describedby") ?? "").split(" ")
    .map((id) => document.getElementById(id)?.textContent);
  expect(describedText(code)).toEqual(["Taken."]);
  expect(describedText(source)).toEqual(["Unknown source."]);
  expect(code.getAttribute("aria-invalid")).toBe("true");
  expect(source.getAttribute("aria-invalid")).toBe("true");
  expect(submit).toHaveBeenCalledTimes(1);
});

describe("where focus is when the dialog opens", () => {
  function openDialog(fields: readonly DescriptorField[], options: { initialValues?: Record<string, unknown>; widgets?: WidgetMap } = {}) {
    return render(<AppRuntimeProvider runtime={{ widgets: options.widgets ?? defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Rename" fields={fields} initialValues={options.initialValues}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={vi.fn().mockResolvedValue(ok)} />
    </AppRuntimeProvider>);
  }
  // A validation a focus or a blur started runs after the event; a macrotask lets it settle before asserting.
  const settle = () => act(() => new Promise((resolve) => setTimeout(resolve, 20)));
  const required = "This field is required.";

  test("the first field has focus; no message shows, nor once focus moves on to the next field", async () => {
    openDialog([{ name: "name", label: "Name", required: true }, { name: "note", label: "Note", required: true }]);
    const dialog = await screen.findByRole("dialog", { name: "Rename" });
    const name = screen.getByRole("textbox", { name: "Name" });
    await waitFor(() => expect(document.activeElement).toBe(name));
    await settle();
    expect(screen.queryByText(required)).toBeNull();
    expect(dialog.querySelector("[aria-invalid='true']")).toBeNull();

    const note = screen.getByRole("textbox", { name: "Note" });
    act(() => note.focus());
    expect(document.activeElement).toBe(note);
    await settle();
    expect(screen.queryByText(required)).toBeNull();
    expect(dialog.querySelector("[aria-invalid='true']")).toBeNull();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Save" }).disabled).toBe(true);
  });

  test.each<[string, DescriptorField]>([
    ["shown by its widget as text", { name: "code", label: "Code", readOnly: true }],
    ["a control of its own that renders a read-only input", { name: "code", label: "Code", readOnly: true, control: ({ id, value, readOnly }) => (
      <input id={id} readOnly={readOnly} value={typeof value === "string" ? value : ""} onChange={() => undefined} />
    ) }],
  ])("a read-only first field, %s, is passed over: the next field has focus", async (_shape, code) => {
    openDialog([code, { name: "name", label: "Name" }], { initialValues: { code: "C-1" } });
    await screen.findByRole("dialog", { name: "Rename" });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Name" })));
  });

  test("guard: a dialog whose fields are all read-only keeps focus where Base UI puts it: on Close", async () => {
    openDialog([{ name: "code", label: "Code", readOnly: true }, { name: "name", label: "Name", readOnly: true }],
      { initialValues: { code: "C-1", name: "First" } });
    await screen.findByRole("dialog", { name: "Rename" });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" })));
  });

  const objectFirst = deserializeFormSpec({ type: "object", properties: {
    retry: { type: "object", widget: "object", label: "Retry", properties: {
      handle: { type: "string", label: "Handle" },
      delay: { type: "integer", label: "Delay" },
    } },
    code: { type: "string", label: "Code" },
  } }, defaultWidgets);

  test("an object field first: its first member's control has focus", async () => {
    // The object widget's code loads at its first render; a dialog opened after that shows its members at once.
    openDialog(objectFirst);
    await screen.findByLabelText("Handle");
    cleanup();
    openDialog(objectFirst);
    await screen.findByRole("dialog", { name: "Rename" });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Handle")));
  });

  test("a first field whose widget's code is still loading when the dialog opens is passed over: the next field has focus, and keeps it once the members show", async () => {
    // A widget of its own, so its code has never loaded in this run, whichever test ran before.
    const loading = lazyWidget(() => import("./StructuredField").then((module) => module.objectWidget), { edit: true });
    const fields = objectFirst.map((field) => field.name === "retry" ? { ...field, widget: "loadingObject" } : field);
    openDialog(fields, { widgets: { ...defaultWidgets, loadingObject: loading } });
    await screen.findByRole("dialog", { name: "Rename" });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Code" })));
    await screen.findByLabelText("Handle");
    await settle();
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Code" }));
  });
});

describe("a required member of an object field", () => {
  // A FormSpec object whose `required` names a member: the member is `required`, without a presence flag.
  const authFields = deserializeFormSpec({ type: "object", properties: {
    auth: { type: "object", widget: "object", label: "Auth", required: ["token"], properties: {
      token: { type: "string", label: "Token" },
      note: { type: "string", label: "Note" },
    } },
  } }, defaultWidgets);
  const requiredMessage = "This field is required.";
  const messageUnder = (label: string) => screen.getByText(label).closest('[data-layout="stack"]')?.textContent ?? "";
  const settle = () => act(() => new Promise((resolve) => setTimeout(resolve, 20)));

  test("absent when the dialog opens: Save, pressed, submits nothing and shows that the member is required under it; filled, it submits with the member", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Connect" fields={authFields} initialValues={{ auth: {} }}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);
    const token = await screen.findByLabelText("Token") as HTMLInputElement;
    const save = screen.getByRole<HTMLButtonElement>("button", { name: "Save" });
    // Measured: Save is enabled at the opening, before any change, since the object field itself is not required.
    expect(save.disabled).toBe(false);

    fireEvent.click(save);
    expect(await screen.findByText(requiredMessage)).toBeTruthy();
    expect(messageUnder("Token")).toContain(requiredMessage);
    expect(token.getAttribute("aria-invalid")).toBe("true");
    await settle();
    expect(submit).not.toHaveBeenCalled();

    fireEvent.change(token, { target: { value: "abc" } });
    await waitFor(() => expect(screen.queryByText(requiredMessage)).toBeNull());
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]![0]).toStrictEqual({ auth: { token: "abc" } });
  });

  test("holding a blank text, the member is a value: no message shows, and Save submits it as held", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Connect" fields={authFields} initialValues={{ auth: { token: " " } }}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);
    await screen.findByLabelText("Token");
    const save = screen.getByRole<HTMLButtonElement>("button", { name: "Save" });
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]![0]).toStrictEqual({ auth: { token: " " } });
    expect(screen.queryByText(requiredMessage)).toBeNull();
  });

  test("guard: a member that is required and nullable, holding null, is accepted", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    const fields = deserializeFormSpec({ type: "object", properties: {
      auth: { type: "object", widget: "object", label: "Auth", required: ["token"], properties: {
        token: { anyOf: [{ type: "string" }, { type: "null" }], label: "Token" },
        note: { type: "string", label: "Note" },
      } },
    } }, defaultWidgets);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Connect" fields={fields} initialValues={{ auth: { token: null, note: "n" } }}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);
    await screen.findByLabelText("Note");
    const save = screen.getByRole<HTMLButtonElement>("button", { name: "Save" });
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]![0]).toStrictEqual({ auth: { token: null, note: "n" } });
    expect(screen.queryByText(requiredMessage)).toBeNull();
  });
});

test("a bounded optional field opened with an own key holding undefined shows no message, and Save submits", async () => {
  const submit = vi.fn().mockResolvedValue(ok);
  render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <MutationDialog open onOpenChange={vi.fn()} title="Rename" fields={[{ name: "nickname", label: "Nickname", maxLength: 64 }]}
      initialValues={{ nickname: undefined }} submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
  </AppRuntimeProvider>);
  const nickname = await screen.findByLabelText("Nickname");
  const save = screen.getByRole<HTMLButtonElement>("button", { name: "Save" });
  await waitFor(() => expect(save.disabled).toBe(false));
  fireEvent.click(save);
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  expect(screen.queryByText("This field is required.")).toBeNull();
  expect(nickname.getAttribute("aria-invalid")).toBeNull();
});

describe("a member the person cannot see or change", () => {
  // A hand-declared object field: `handle` holds a value, `secret` is required and left out.
  const retry = (secret: Partial<DescriptorField>): DescriptorField => ({
    name: "retry", label: "Retry", widget: "object", objectTemplate: [
      { name: "handle", label: "Handle", required: true },
      { name: "secret", label: "Secret", required: true, ...secret },
    ],
  });
  const requiredMessage = "This field is required.";
  const settle = () => act(() => new Promise((resolve) => setTimeout(resolve, 20)));
  async function renderRetry(secret: Partial<DescriptorField>) {
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Retry" fields={[retry(secret)]} initialValues={{ retry: { handle: "abc" } }}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);
    const handle = await screen.findByLabelText("Handle");
    return { submit, handle, save: screen.getByRole<HTMLButtonElement>("button", { name: "Save" }) };
  }

  test.each<[string, Partial<DescriptorField>]>([
    ["hidden", { hidden: true }],
    ["read-only", { readOnly: true }],
  ])("a %s required member left out does not block: after an edit of a sibling no message shows, Save is enabled, and the submit sends exactly the values held", async (_state, secret) => {
    const { submit, handle, save } = await renderRetry(secret);
    fireEvent.change(handle, { target: { value: "abcd" } });
    await settle();
    expect(screen.queryByText(requiredMessage)).toBeNull();
    expect(document.querySelector('[aria-invalid="true"]')).toBeNull();
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]![0]).toStrictEqual({ retry: { handle: "abcd" } });
  });

  test("guard: a visible, editable required member left out still blocks: after an edit of a sibling its message shows under it and nothing is submitted", async () => {
    const { submit, handle, save } = await renderRetry({});
    fireEvent.change(handle, { target: { value: "abcd" } });
    expect(await screen.findByText(requiredMessage)).toBeTruthy();
    expect(screen.getByText("Secret").closest('[data-layout="stack"]')?.textContent).toContain(requiredMessage);
    expect(save.disabled).toBe(true);
    fireEvent.submit(save.closest("form")!);
    await settle();
    expect(submit).not.toHaveBeenCalled();
  });

  test("a blank item of a list whose item template is read-only is not refused: Save is enabled and the submit sends the item as held", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Tags" initialValues={{ tags: [" "], note: "" }}
        fields={[
          { name: "tags", label: "Tags", widget: "list", itemTemplate: { name: "item", label: "Tag", required: true, readOnly: true } },
          { name: "note", label: "Note" },
        ]}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);
    fireEvent.change(await screen.findByLabelText("Note"), { target: { value: "n" } });
    await settle();
    expect(screen.queryByText(requiredMessage)).toBeNull();
    const save = screen.getByRole<HTMLButtonElement>("button", { name: "Save" });
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]![0]).toStrictEqual({ tags: [" "], note: "n" });
  });

  test("an item added to a FormSpec list of texts is a value: no message shows, Save stays enabled, and the submit sends the empty item", async () => {
    const submit = vi.fn().mockResolvedValue(ok);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Tags" initialValues={{ tags: [], note: "" }}
        fields={[
          ...deserializeFormSpec({ type: "object", properties: {
            tags: { type: "array", widget: "list", label: "Tags", items: { type: "string", label: "Tag" } },
          } }, defaultWidgets),
          { name: "note", label: "Note" },
        ]}
        submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);
    await screen.findByLabelText("Note");
    // The list widget mounts after the plain fields: wait for its button before acting on it.
    fireEvent.click(await screen.findByRole("button", { name: "Add item" }));
    await screen.findByLabelText("Tag");
    await settle();
    expect(screen.queryByText(requiredMessage)).toBeNull();
    const save = screen.getByRole<HTMLButtonElement>("button", { name: "Save" });
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]![0]).toStrictEqual({ tags: [""], note: "" });
  });
});
