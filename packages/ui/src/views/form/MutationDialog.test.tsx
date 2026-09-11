// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { afterEach, describe, expect, test, vi } from "vitest";
import { ModelMetadataProvider, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";

import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import {
  LabeledDescriptorField,
  MutationDialog,
  emptyValueForField,
  mutationDialogValueCodecs,
} from "./MutationDialog";
import { deserializeFormSpec } from "./form-spec";

const parseRawValues = (values: Readonly<Record<string, unknown>>) => values;

describe("MutationDialog", () => {
  afterEach(cleanup);

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
    let resolve!: (value: string) => void;
    const onSubmitted = vi.fn();
    const submit = vi.fn(() => new Promise<string>((done) => { resolve = done; }));
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
    resolve("old result");
    await Promise.resolve();
    expect(onSubmitted).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  test("allows an optional-only dialog to submit its initial omitted value", async () => {
    const submit = vi.fn().mockResolvedValue({ ok: true });
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Optional" fields={[{ name: "input", label: "Input", widget: "json", omittable: true, nullable: true }]}
        submitLabel="Start" parseValues={(values) => values} onSubmit={submit} />
    </AppRuntimeProvider>);
    const button = screen.getByRole("button", { name: "Start" }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(submit).toHaveBeenCalledWith({}));
  });

  test("a domain readiness gate blocks buttons and form submission until ready", async () => {
    const submit = vi.fn();
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
    const submit = vi.fn().mockRejectedValueOnce(new Error("Try again")).mockResolvedValueOnce({ ok: true });
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

  test("resolves dependent descriptor options from current values without replacing an unknown value", async () => {
    const submit = vi.fn();
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
              name: "outcome",
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

    await waitFor(() => expect(submit).toHaveBeenCalledWith({ source: "b", outcome: "legacy" }));
  });

  test("revalidates resolved required and read-only state while retaining declared field identity", async () => {
    const submit = vi.fn();
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
            resolve: (values) => ({
              name: "wrong-name",
              required: values.mode === "custom",
              readOnly: values.mode !== "custom",
            }),
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
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ mode: "custom", value: "kept" }));
  });

  test("a required nullable FormSpec value accepts explicit null", async () => {
    const submit = vi.fn();
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <MutationDialog open onOpenChange={vi.fn()} title="Nullable" fields={[
        { name: "note", label: "Note", required: true, nullable: true, presenceRequired: true },
      ]} initialValues={{ note: null }} submitLabel="Save" parseValues={parseRawValues} onSubmit={submit} />
    </AppRuntimeProvider>);

    const button = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ note: null }));
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
        submitLabel="Save" parseValues={parseRawValues} onSubmit={vi.fn()} />
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
          onSubmit={vi.fn()}
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

  test("uses schema-safe empty values for descriptor field kinds", () => {
    expect(emptyValueForField({ kind: "integer" })).toBeNull();
    expect(emptyValueForField({ kind: "number" })).toBeNull();
    expect(emptyValueForField({ kind: "any" })).toBeNull();
    expect(emptyValueForField({ kind: "array" })).toEqual([]);
    expect(emptyValueForField({ kind: "object" })).toEqual({});
    expect(emptyValueForField({ kind: "boolean" })).toBe(false);
    expect(emptyValueForField({ kind: "any", widget: "select" })).toBe("");
    expect(emptyValueForField({ kind: "string" })).toBe("");
  });

  test("an unknown relation degrades to a disabled control and development warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <ModelMetadataProvider
          metadata={schemaFieldMetadataFromDataResources([testDataResource("parties.Party")])}
        >
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
              onSubmit={vi.fn()}
            />
          </AppRuntimeProvider>
        </ModelMetadataProvider>
      </QueryClientProvider>,
    );

    expect(
      (screen.getByRole("button", { name: "Owner" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(warn).toHaveBeenCalledWith(
      expect.stringMatching(/mutation dialog relation.*missing\.Person/),
    );
    warn.mockRestore();
  });

  test("decodes raw controls before submitting typed values", async () => {
    const onSubmit = vi.fn();
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
      expect(onSubmit).toHaveBeenCalledWith({ name: "Ada", note: null }),
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
          onSubmit={() => pendingSubmit}
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
