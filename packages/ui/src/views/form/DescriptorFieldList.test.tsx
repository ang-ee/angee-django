// @vitest-environment happy-dom

import { StrictMode, useState, type ReactElement, type ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { EditorView } from "@codemirror/view";
import { FormProvider, useForm, type DefaultValues, type FieldValues, type UseFormReturn } from "react-hook-form";
import { describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import { ToastProvider } from "../../feedback";
import { defaultWidgets, type WidgetDefinition } from "../../widgets";
import { RelationPicker } from "../relation/RelationPicker";
import { DescriptorFieldList, LabeledDescriptorField, type DescriptorField } from "./DescriptorFieldList";
import { deserializeFormSpec } from "./form-spec";
import { useActionForm } from "./use-action-form";
import { ActionFormProvider } from "./ActionFormProvider";
import { useFieldValidation } from "./use-field-validation";

type Values = Record<string, unknown>;

function FormHarness<TValues extends Values = Values>({
  fields,
  defaultValues,
  children,
  readOnly,
  unregisterHidden,
  onSubmit = () => undefined,
}: {
  fields: readonly DescriptorField[];
  defaultValues: DefaultValues<TValues>;
  children?: (form: UseFormReturn<TValues>) => ReactNode;
  readOnly?: boolean;
  unregisterHidden?: boolean;
  onSubmit?: (values: TValues) => void | Promise<void>;
}): ReactElement {
  const form = useForm<TValues>({ defaultValues });
  return <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <FormOwner form={form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        <DescriptorFieldList fields={fields} readOnly={readOnly} unregisterHidden={unregisterHidden} />
        {children?.(form)}
        <button type="submit">Save</button>
      </form>
    </FormOwner>
  </AppRuntimeProvider>;
}

/** The provider a DescriptorFieldList requires, over a bare react-hook-form form. */
function FormOwner<TValues extends FieldValues>({ form, children }: { form: UseFormReturn<TValues>; children: ReactNode }): ReactElement {
  const { registerFieldValidation } = useFieldValidation();
  return <ActionFormProvider {...form} registerFieldValidation={registerFieldValidation}>{children}</ActionFormProvider>;
}

describe("DescriptorFieldList", () => {
  test("relation metadata preserves an explicitly declared registry widget", () => {
    const edit: WidgetDefinition["edit"] = ({ onChange }) => <button onClick={() => onChange?.("chosen")}>Custom choice</button>;
    const onChange = vi.fn();
    render(<AppRuntimeProvider runtime={{ widgets: { custom: { read: edit, edit } } }}>
      <LabeledDescriptorField field={{ name: "target", label: "Target", widget: "custom",
        relation: { resource: "notes.Note", labelField: "display_name" } }}
        value={null} onChange={onChange} />
    </AppRuntimeProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Custom choice" }));
    expect(onChange).toHaveBeenCalledWith("chosen");
  });

  test("structured values containing target remain atomic native form values", async () => {
    const submit = vi.fn();
    const fields = deserializeFormSpec({ properties: { config: {
      type: "object", properties: { target: { type: "string" }, retained: { type: "integer" } },
    } } }, defaultWidgets);
    render(<FormHarness fields={fields} defaultValues={{ config: { target: "first", retained: 7 } }} onSubmit={submit} />);
    fireEvent.change(await screen.findByRole("textbox", { name: "Target" }), { target: { value: "second" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ config: { target: "second", retained: 7 } }, expect.anything()));
  });

  test("humanizes schema field names when no field title is declared", async () => {
    const fields = deserializeFormSpec({ properties: {
      note: { type: "string" },
      source_line: { type: "object", properties: { original_text: { type: "string" } } },
    } }, defaultWidgets);
    render(<FormHarness fields={fields} defaultValues={{ note: "", source_line: { original_text: "" } }} />);
    const visibleLabel = screen.getByText("Note");
    expect(screen.getByRole("textbox", { name: visibleLabel.textContent ?? "" })).toBeTruthy();
    expect(screen.getByText("Source Line")).toBeTruthy();
    expect(await screen.findByText("Original Text")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Original Text" })).toBeTruthy();
  });

  test("registry widgets receive live sibling values in descriptor forms", () => {
    const read: WidgetDefinition["read"] = ({ row }) => <output aria-label="Sibling model">{row && typeof row === "object" && "model" in row ? String(row.model) : ""}</output>;
    function Harness() {
      const form = useForm({ defaultValues: { model: "notes.Note", condition: {} } });
      return <AppRuntimeProvider runtime={{ widgets: { ...defaultWidgets, sibling: { read, edit: read } } }}>
        <FormOwner form={form}><DescriptorFieldList fields={[
          { name: "model", label: "Model" }, { name: "condition", widget: "sibling" },
        ]} /></FormOwner>
      </AppRuntimeProvider>;
    }
    render(<Harness />);
    expect(screen.getByLabelText("Sibling model").textContent).toBe("notes.Note");
    fireEvent.change(screen.getByRole("textbox", { name: "Model" }), { target: { value: "messaging.Message" } });
    expect(screen.getByLabelText("Sibling model").textContent).toBe("messaging.Message");
  });

  test.each([false, true])("keeps malformed JSON drafts through asynchronous validation (nested: %s)", async (nested) => {
    const submit = vi.fn(async () => ({ status: "ok" as const, data: null }));
    let finishValidation: (() => void) | undefined;
    const config = { type: "object" as const, title: "Config", widget: "json", properties: { name: { type: "string" as const } } };
    const fields = deserializeFormSpec({ properties: nested ? { details: { type: "object", properties: { config } } } : { config } }, defaultWidgets);
    const values = (name: string) => nested ? { details: { config: { name } } } : { config: { name } };
    function Harness() {
      const action = useActionForm<Record<string, unknown>>({
        defaultValues: values("Original"), submit,
        resolver: (collected) => new Promise((resolve) => { finishValidation = () => resolve({ values: collected, errors: {} }); }),
      });
      return <ActionFormProvider {...action.form}>
        <form onSubmit={(event) => { event.preventDefault(); void action.run(); }}>
          <DescriptorFieldList fields={fields} />
          <button type="submit">Save</button><output aria-label="Submitting">{String(action.submitting)}</output>
        </form>
      </ActionFormProvider>;
    }
    render(<ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><Harness /></AppRuntimeProvider></ToastProvider>);
    const input = await screen.findByRole("textbox", { name: "Config" });
    const editor = EditorView.findFromDOM(input)!;
    act(() => editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: "{" } }));
    fireEvent.blur(input);
    await screen.findByText("Invalid JSON");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(input.getAttribute("contenteditable")).toBe("false"));
    expect(EditorView.findFromDOM(input)).toBe(editor);
    await act(async () => finishValidation?.());
    const error = await screen.findByText("Enter a valid value.");
    await waitFor(() => expect(screen.getByLabelText("Submitting").textContent).toBe("false"));
    expect(submit).not.toHaveBeenCalled();
    expect(EditorView.findFromDOM(input)).toBe(editor);
    expect(editor.state.doc.toString()).toBe("{");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(error.id);

    act(() => editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: '{"name":"Updated"}' } }));
    fireEvent.blur(input);
    await waitFor(() => expect(screen.queryByText("Invalid JSON")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(input.getAttribute("contenteditable")).toBe("false"));
    await act(async () => finishValidation?.());
    await waitFor(() => expect(submit).toHaveBeenCalledWith(values("Updated"), { errorNotification: false }));
  });

  test.each(["text", "readOnly"] as const)("discards old editor validity when changing the same field to %s", async (nextMode) => {
    const submit = vi.fn(async () => ({ status: "ok" as const, data: null }));
    function Harness() {
      const [mode, setMode] = useState<"json" | "text" | "readOnly">("json");
      const action = useActionForm<Record<string, unknown>>({ defaultValues: { payload: { note: "Retained" } }, submit });
      return <ActionFormProvider {...action.form}>
        <form onSubmit={(event) => { event.preventDefault(); void action.run(); }}>
          <DescriptorFieldList fields={[{ name: "payload", label: "Payload", widget: mode === "text" ? "text" : "json", readOnly: mode === "readOnly" }]} />
          <button type="button" onClick={() => {
            action.form.reset({ payload: nextMode === "text" ? "Updated" : { note: "Retained" } });
            setMode(nextMode);
          }}>Switch control</button>
          <button type="button" onClick={() => setMode("json")}>Edit JSON</button>
          <button type="submit">Save</button>
        </form>
      </ActionFormProvider>;
    }
    render(<ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><Harness /></AppRuntimeProvider></ToastProvider>);
    const input = await screen.findByRole("textbox", { name: "Payload" });
    const editor = EditorView.findFromDOM(input)!;
    act(() => editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: "{" } }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Enter a valid value.");
    expect(submit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Switch control" }));
    if (nextMode === "readOnly") {
      expect(screen.queryByRole("textbox", { name: "Payload" })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Edit JSON" }));
      const replacement = EditorView.findFromDOM(await screen.findByRole("textbox", { name: "Payload" }))!;
      expect(replacement).not.toBe(editor);
      expect(JSON.parse(replacement.state.doc.toString())).toEqual({ note: "Retained" });
    } else {
      expect((screen.getByRole("textbox", { name: "Payload" }) as HTMLInputElement).value).toBe("Updated");
    }
    expect(screen.queryByText("Invalid JSON")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ payload: nextMode === "text" ? "Updated" : { note: "Retained" } }, { errorNotification: false }));
  });

  test("default object projection keeps nested read-only values out of editable controls", async () => {
    const submit = vi.fn();
    const fields = deserializeFormSpec({ properties: { details: { type: "object", properties: {
      identity: { type: "string", title: "Identity", readOnly: true },
      note: { type: "string", title: "Note" },
    } } } }, defaultWidgets);
    render(<FormHarness fields={fields} defaultValues={{ details: { identity: "Retained", note: "Original" } }} onSubmit={submit} />);
    expect(screen.queryByRole("textbox", { name: "Identity" })).toBeNull();
    expect(await screen.findByText("Retained")).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Note" }), { target: { value: "Updated" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit.mock.calls[0]?.[0]).toEqual({ details: { identity: "Retained", note: "Updated" } }));
  });

  test("names the required form owner when rendered without its provider", () => {
    expect(() => render(<DescriptorFieldList fields={[]} />)).toThrow(
      "DescriptorFieldList requires a React Hook Form FormProvider.",
    );
  });

  test("resolves visibility and read-only presentation while preserving declared names and hidden values", async () => {
    const submit = vi.fn();
    render(<FormHarness defaultValues={{ mode: "fixed", detail: "Original", token: "retained" }} onSubmit={submit}
      fields={[
        { name: "mode", label: "Mode" },
        { name: "detail", label: "Detail", showWhen: (values) => values.mode !== "hidden",
          // This cast hides the returned `name` from the compiler; the declared name must still win.
          resolve: (values) => ({ name: "ignored", readOnly: values.mode === "fixed" }) as Omit<DescriptorField, "name"> },
        { name: "token", hidden: true },
      ]} />);

    expect(screen.queryByRole("textbox", { name: "Detail" })).toBeNull();
    expect(screen.getByText("Original")).toBeTruthy();
    const mode = screen.getByRole("textbox", { name: "Mode" });
    fireEvent.change(mode, { target: { value: "custom" } });
    fireEvent.change(await screen.findByRole("textbox", { name: "Detail" }), { target: { value: "Edited" } });
    fireEvent.change(mode, { target: { value: "hidden" } });
    expect(screen.queryByText("Detail")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit.mock.calls[0]?.[0]).toEqual({ mode: "hidden", detail: "Edited", token: "retained" }));
  });

  test.each([
    { path: "detail", label: "Detail", strict: false },
    { path: "detail", label: "Detail", strict: true },
    { path: "config.url", label: "Url", strict: false },
    { path: "config.url", label: "Url", strict: true },
  ])("with unregisterHidden, a hidden $path is not submitted and comes back showing its initial value (StrictMode: $strict)", async ({ path, label, strict }) => {
    const submit = vi.fn();
    const harness = <FormHarness unregisterHidden onSubmit={submit}
      defaultValues={{ mode: "on", detail: "initial", secret: "kept", token: "untouched", config: { url: "initial", other: "stays" } }}
      fields={[
        { name: "mode", label: "Mode" },
        { name: path, label, showWhen: (values) => values.mode === "on" },
        { name: "secret", label: "Secret", hidden: true },
      ]} />;
    render(strict ? <StrictMode>{harness}</StrictMode> : harness);
    const input = (name: string) => screen.getByRole("textbox", { name }) as HTMLInputElement;
    fireEvent.change(input(label), { target: { value: "typed" } });
    fireEvent.change(input("Mode"), { target: { value: "off" } });
    expect(screen.queryByRole("textbox", { name: label })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    const hidden = submit.mock.calls[0]![0] as Values;
    const [head, leaf] = path.split(".") as [string, string?];
    // Hidden means gone from the values; what no shown field leaves (a sibling, an undeclared value, a hidden field) stays.
    expect(leaf ? Object.hasOwn(hidden[head] as Values, leaf) : Object.hasOwn(hidden, head)).toBe(false);
    expect(hidden).toEqual(leaf
      ? { mode: "off", detail: "initial", secret: "kept", token: "untouched", config: { other: "stays" } }
      : { mode: "off", secret: "kept", token: "untouched", config: { url: "initial", other: "stays" } });

    fireEvent.change(input("Mode"), { target: { value: "on" } });
    // The returning control shows its initial value at once, not after an effect restores it.
    expect(input(label).value).toBe("initial");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
    expect(submit.mock.calls[1]![0]).toEqual({ mode: "on", detail: "initial", secret: "kept", token: "untouched", config: { url: "initial", other: "stays" } });
  });

  test("with unregisterHidden, a path that leaves the list stays while a shown field still lives under it", async () => {
    const submit = vi.fn();
    render(<FormHarness unregisterHidden onSubmit={submit}
      defaultValues={{ mode: "all", config: { url: "initial", other: "stays" } }}
      fields={[
        { name: "mode", label: "Mode" },
        // The whole object is declared only in one mode; its `url` is always shown.
        { name: "config", label: "Config", hidden: true, showWhen: (values) => values.mode === "all" },
        { name: "config.url", label: "Url" },
      ]} />);
    const input = (name: string) => screen.getByRole("textbox", { name }) as HTMLInputElement;
    fireEvent.change(input("Url"), { target: { value: "typed" } });
    fireEvent.change(input("Mode"), { target: { value: "one" } });
    expect(input("Url").value).toBe("typed");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit.mock.calls[0]?.[0]).toEqual({ mode: "one", config: { url: "typed", other: "stays" } }));
  });

  test("with unregisterHidden, a path that leaves the list stays while a shown field still holds it", async () => {
    const submit = vi.fn();
    render(<FormHarness unregisterHidden onSubmit={submit}
      defaultValues={{ mode: "on", config: { url: "initial", other: "stays" } }}
      fields={[
        { name: "mode", label: "Mode" },
        // The whole object is always held; its `url` has a control of its own in one mode.
        { name: "config", label: "Config", hidden: true },
        { name: "config.url", label: "Url", showWhen: (values) => values.mode === "on" },
      ]} />);
    const input = (name: string) => screen.getByRole("textbox", { name }) as HTMLInputElement;
    fireEvent.change(input("Url"), { target: { value: "typed" } });
    fireEvent.change(input("Mode"), { target: { value: "off" } });
    expect(screen.queryByRole("textbox", { name: "Url" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit.mock.calls[0]?.[0]).toEqual({ mode: "off", config: { url: "typed", other: "stays" } }));
  });

  test("without unregisterHidden, a field the list stops showing keeps its value: one form, the selected item's fields", async () => {
    const submit = vi.fn();
    function Selection(): ReactElement {
      const [selected, setSelected] = useState("a");
      const form = useForm({ defaultValues: { a: { label: "A" }, b: { label: "B" } } });
      return <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><FormOwner form={form}>
        <form onSubmit={form.handleSubmit(submit)}>
          <DescriptorFieldList fields={[{ name: `${selected}.label`, label: `Label ${selected}` }]} />
          <button type="button" onClick={() => setSelected(selected === "a" ? "b" : "a")}>Select other</button>
          <button type="submit">Save</button>
        </form>
      </FormOwner></AppRuntimeProvider>;
    }
    render(<Selection />);
    fireEvent.change(screen.getByRole("textbox", { name: "Label a" }), { target: { value: "Typed A" } });
    fireEvent.click(screen.getByRole("button", { name: "Select other" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Label b" }), { target: { value: "Typed B" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit.mock.calls[0]?.[0]).toEqual({ a: { label: "Typed A" }, b: { label: "Typed B" } }));
    fireEvent.click(screen.getByRole("button", { name: "Select other" }));
    expect((screen.getByRole("textbox", { name: "Label a" }) as HTMLInputElement).value).toBe("Typed A");
  });

  test("shares sibling values with custom controls and applies prefills in the same form", async () => {
    const submit = vi.fn();
    render(<FormHarness defaultValues={{ source: "", title: "", note: "" }} onSubmit={submit} fields={[
      { name: "source", label: "Source", prefill: (value, values) => ({ title: `${String(value)} ${String(values.note)}`.trim() }),
        control: ({ id, value, onChange, dialogValues }) => <input id={id}
          value={typeof value === "string" ? value : ""}
          placeholder={String(dialogValues.note)} onChange={(event) => onChange(event.target.value)} /> },
      { name: "title", label: "Title" },
      { name: "note", label: "Note" },
    ]}>{(form) => <button type="button" onClick={() => form.setError("title", { message: "Choose a title" })}>Show error</button>}</FormHarness>);

    fireEvent.click(screen.getByRole("button", { name: "Show error" }));
    expect(await screen.findByText("Choose a title")).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Note" }), { target: { value: "Sibling" } });
    const source = screen.getByRole("textbox", { name: "Source" });
    expect(source.getAttribute("placeholder")).toBe("Sibling");
    fireEvent.change(source, { target: { value: "Seeded" } });
    await waitFor(() => expect(screen.queryByText("Choose a title")).toBeNull());
    // A prefill reads the form's current values beside the field's new one.
    expect((screen.getByRole("textbox", { name: "Title" }) as HTMLInputElement).value).toBe("Seeded Sibling");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit.mock.calls[0]?.[0]).toEqual({ source: "Seeded", title: "Seeded Sibling", note: "Sibling" }));
  });

  test("a dirty-preserving preset keeps what the person typed, replaces what it names, and seeds the rest clean", async () => {
    const submit = vi.fn();
    render(<FormHarness defaultValues={{ kind: "a", secret: "", config: "", style: "" }} onSubmit={submit} fields={[
      { name: "kind", label: "Kind", prefillPreserveDirty: true, prefillReplace: ["config"],
        prefill: (value) => ({ secret: `${String(value)} secret`, config: `${String(value)} config`, style: `${String(value)} style` }) },
      { name: "secret", label: "Secret" },
      { name: "config", label: "Config" },
      { name: "style", label: "Style" },
    ]}>{(form) => <output aria-label="Dirty">{Object.keys(form.formState.dirtyFields).sort().join(",")}</output>}</FormHarness>);
    const input = (name: string) => screen.getByRole("textbox", { name }) as HTMLInputElement;
    fireEvent.change(input("Secret"), { target: { value: "typed secret" } });
    fireEvent.change(input("Config"), { target: { value: "typed config" } });

    fireEvent.change(input("Kind"), { target: { value: "b" } });
    expect(input("Secret").value).toBe("typed secret");
    expect(input("Config").value).toBe("b config");
    expect(input("Style").value).toBe("b style");
    expect(screen.getByLabelText("Dirty").textContent).toBe("config,kind,secret");

    // Back on its loaded value the form reads every changed value as dirty; the seed still in place stays the preset's.
    fireEvent.change(input("Kind"), { target: { value: "a" } });
    expect(input("Secret").value).toBe("typed secret");
    expect(input("Config").value).toBe("a config");
    expect(input("Style").value).toBe("a style");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit.mock.calls[0]?.[0]).toEqual({ kind: "a", secret: "typed secret", config: "a config", style: "a style" }));
  });

  test("a dirty-preserving preset keeps a target the person cleared back to its loaded value", () => {
    render(<FormHarness defaultValues={{ kind: "a", secret: "" }} fields={[
      { name: "kind", label: "Kind", prefillPreserveDirty: true, prefill: (value) => ({ secret: `${String(value)} secret` }) },
      { name: "secret", label: "Secret" },
    ]} />);
    const input = (name: string) => screen.getByRole("textbox", { name }) as HTMLInputElement;
    fireEvent.change(input("Secret"), { target: { value: "typed" } });
    fireEvent.change(input("Secret"), { target: { value: "" } });
    fireEvent.change(input("Kind"), { target: { value: "b" } });
    expect(input("Secret").value).toBe("");
  });

  test("a dirty-preserving preset still owns its seed after another field was changed and cleared", () => {
    render(<FormHarness defaultValues={{ kind: "a", secret: "", other: "" }} fields={[
      { name: "kind", label: "Kind", prefillPreserveDirty: true, prefill: (value) => ({ secret: `${String(value)} secret` }) },
      { name: "secret", label: "Secret" },
      { name: "other", label: "Other" },
    ]}>{(form) => <output aria-label="Form dirty">{String(form.formState.isDirty)}</output>}</FormHarness>);
    const input = (name: string) => screen.getByRole("textbox", { name }) as HTMLInputElement;
    fireEvent.change(input("Kind"), { target: { value: "b" } });
    expect(input("Secret").value).toBe("b secret");
    // Back on its loaded value while the form stays dirty, react-hook-form reads every changed value,
    // the seeded one included, as dirty: what the preset wrote there is still the preset's.
    fireEvent.change(input("Other"), { target: { value: "x" } });
    fireEvent.change(input("Other"), { target: { value: "" } });
    expect(screen.getByLabelText("Form dirty").textContent).toBe("true");
    fireEvent.change(input("Kind"), { target: { value: "c" } });
    expect(input("Secret").value).toBe("c secret");
  });

  test("lists under one provider share what a preset wrote: another list's seed is not the person's", () => {
    function Harness(): ReactElement {
      const form = useForm({ defaultValues: { kind: "a", mode: "m", style: "" } });
      return <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><FormOwner form={form}>
        <DescriptorFieldList fields={[{ name: "kind", label: "Kind", prefillPreserveDirty: true,
          prefill: (value) => ({ style: `kind ${String(value)}` }) }]} />
        <DescriptorFieldList fields={[
          { name: "mode", label: "Mode", prefillPreserveDirty: true, prefill: (value) => ({ style: `mode ${String(value)}` }) },
          { name: "style", label: "Style" },
        ]} />
      </FormOwner></AppRuntimeProvider>;
    }
    render(<Harness />);
    const input = (name: string) => screen.getByRole("textbox", { name }) as HTMLInputElement;
    fireEvent.change(input("Kind"), { target: { value: "b" } });
    // Back on its loaded value, the form reads the seeded style as dirty; it still holds the first list's seed.
    fireEvent.change(input("Kind"), { target: { value: "a" } });
    expect(input("Style").value).toBe("kind a");
    fireEvent.change(input("Mode"), { target: { value: "n" } });
    expect(input("Style").value).toBe("mode n");
  });

  test("names the action form provider it requires under a bare react-hook-form provider", () => {
    function Bare(): ReactElement {
      const form = useForm();
      return <FormProvider {...form}><DescriptorFieldList fields={[]} /></FormProvider>;
    }
    expect(() => render(<Bare />)).toThrow("DescriptorFieldList requires an ActionFormProvider.");
  });

  test("resets only branch fields while retaining discriminator focus and unrelated form state", async () => {
    const submit = vi.fn();
    render(<FormHarness defaultValues={{ mode: "first", first: "Original", shared: "Original", note: "Retained", token: "untouched" }} onSubmit={submit} fields={[
      { name: "mode", label: "Mode", branchReset: (value) => ({
        fields: value === "first" ? ["first", "shared"] : ["second", "shared", "optional"],
        values: value === "first" ? { first: "First default", shared: "First shared" } : { second: "Second default", shared: "Second shared" },
      }) },
      { name: "first", label: "First", showWhen: (values) => values.mode === "first" },
      { name: "second", label: "Second", showWhen: (values) => values.mode === "second" },
      { name: "optional", label: "Optional", showWhen: (values) => values.mode === "second" },
      { name: "shared", label: "Shared" },
      { name: "note", label: "Note" },
    ]}>{(form) => <>
      <button type="button" onClick={() => form.setError("note", { message: "Keep this error" })}>Show note error</button>
      <output aria-label="Note dirty">{String(form.formState.dirtyFields.note ?? false)}</output>
    </>}</FormHarness>);
    fireEvent.change(screen.getByRole("textbox", { name: "Note" }), { target: { value: "Consumer draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Show note error" }));
    const mode = screen.getByRole("textbox", { name: "Mode" });
    mode.focus();
    fireEvent.change(mode, { target: { value: "second" } });
    expect(document.activeElement).toBe(mode);
    expect(screen.queryByRole("textbox", { name: "First" })).toBeNull();
    expect((screen.getByRole("textbox", { name: "Second" }) as HTMLInputElement).value).toBe("Second default");
    expect((screen.getByRole("textbox", { name: "Shared" }) as HTMLInputElement).value).toBe("Second shared");
    expect(screen.getByText("Keep this error")).toBeTruthy();
    expect(screen.getByLabelText("Note dirty").textContent).toBe("true");
    fireEvent.change(screen.getByRole("textbox", { name: "Optional" }), { target: { value: "Discard when leaving" } });
    fireEvent.change(mode, { target: { value: "first" } });
    fireEvent.change(mode, { target: { value: "second" } });
    expect((screen.getByRole("textbox", { name: "Optional" }) as HTMLInputElement).value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit.mock.calls[0]?.[0]).toEqual({ mode: "second", second: "Second default", shared: "Second shared", optional: undefined, note: "Consumer draft", token: "untouched" }));
  });

  test("guards branch changes from custom controls while read-only or submitting", async () => {
    const branchReset = vi.fn(() => ({ fields: ["detail"], values: { detail: "Reset" } }));
    let complete: (() => void) | undefined;
    let readonlyChange: ((value: unknown) => void) | undefined;
    let submittingChange: ((value: unknown) => void) | undefined;
    const submit = vi.fn((_values: Values) => new Promise<void>((resolve) => { complete = resolve; }));
    render(<FormHarness defaultValues={{ locked: "first", mode: "first", detail: "Retained" }} onSubmit={submit} fields={[
      { name: "locked", label: "Locked", readOnly: true, branchReset, control: ({ onChange }) => {
        readonlyChange = onChange;
        return <span>Locked value</span>;
      } },
      { name: "mode", label: "Mode", branchReset, control: ({ onChange }) => {
        submittingChange = onChange;
        return <span>Mode value</span>;
      } },
    ]} />);
    act(() => readonlyChange?.("second"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    act(() => submittingChange?.("second"));
    expect(branchReset).not.toHaveBeenCalled();
    expect(submit.mock.calls[0]?.[0]).toEqual({ locked: "first", mode: "first", detail: "Retained" });
    await act(async () => complete?.());
  });

  test("binds nested errors, focus and blur to the existing form", async () => {
    const fields = deserializeFormSpec({ type: "object", properties: {
      details: { type: "object", widget: "object", properties: {
        title: { type: "string", label: "Title" },
      } },
    } }, defaultWidgets);
    render(<FormHarness fields={fields} defaultValues={{ details: { title: "" } }}>
      {(form) => <>
        <button type="button" onClick={() => form.setError("details.title", { message: "Choose a title" })}>Show error</button>
        <button type="button" onClick={() => form.setFocus("details")}>Focus field</button>
        <output aria-label="Touched">{String(form.formState.touchedFields.details ?? false)}</output>
      </>}
    </FormHarness>);

    fireEvent.click(screen.getByRole("button", { name: "Show error" }));
    const title = await screen.findByRole("textbox", { name: "Title" });
    const error = await screen.findByText("Choose a title");
    expect(title.getAttribute("aria-invalid")).toBe("true");
    expect(title.getAttribute("aria-describedby")?.split(" ")).toContain(error.id);
    fireEvent.click(screen.getByRole("button", { name: "Focus field" }));
    await waitFor(() => expect(document.activeElement).toBe(title));
    fireEvent.blur(title);
    await waitFor(() => expect(screen.getByLabelText("Touched").textContent).toBe("true"));
    fireEvent.change(title, { target: { value: "Updated" } });
    await waitFor(() => expect(screen.queryByText("Choose a title")).toBeNull());
    expect(title.getAttribute("aria-invalid")).toBeNull();
  });

  test("associates invalid controls with their errors and preserves descriptions when cleared", async () => {
    render(<FormHarness defaultValues={{ title: "", source: "" }} fields={[
      { name: "title", label: "Title", description: "Give the record a name" },
      { name: "source", label: "Source", control: ({ id, value, describedBy, invalid, onChange }) => (
        <RelationPicker id={id} value={typeof value === "string" ? value : ""}
          options={[]} aria-describedby={describedBy} aria-invalid={invalid || undefined}
          aria-label="Source" onChange={onChange} />
      ) },
    ]}>
      {(form) => <>
        <button type="button" onClick={() => {
          form.setError("title", { message: "Choose a title" });
          form.setError("source", { message: "Choose a source" });
        }}>Show errors</button>
        <button type="button" onClick={() => form.clearErrors()}>Clear errors</button>
      </>}
    </FormHarness>);

    fireEvent.click(screen.getByRole("button", { name: "Show errors" }));
    const title = screen.getByRole("textbox", { name: "Title" });
    const source = screen.getByRole("combobox", { name: "Source" });
    const titleError = await screen.findByText("Choose a title");
    const sourceError = screen.getByText("Choose a source");
    const description = screen.getByText("Give the record a name");
    expect(title.getAttribute("aria-invalid")).toBe("true");
    expect(source.getAttribute("aria-invalid")).toBe("true");
    expect(title.getAttribute("aria-describedby")?.split(" ")).toEqual([description.id, titleError.id]);
    expect(source.getAttribute("aria-describedby")).toBe(sourceError.id);

    fireEvent.click(screen.getByRole("button", { name: "Clear errors" }));
    await waitFor(() => expect(title.getAttribute("aria-invalid")).toBeNull());
    expect(source.getAttribute("aria-invalid")).toBeNull();
    expect(title.getAttribute("aria-describedby")).toBe(description.id);
    expect(source.getAttribute("aria-describedby")).toBeNull();
  });

  test("renders nested prefills and host updates without losing sibling values", async () => {
    const submit = vi.fn();
    const details = deserializeFormSpec({ type: "object", properties: {
      details: { type: "object", widget: "object", properties: {
        title: { type: "string", label: "Title" },
        note: { type: "string", label: "Note" },
      } },
    } }, defaultWidgets);
    render(<FormHarness<{ source: string; details: { title: string; note: string } }>
      defaultValues={{ source: "", details: { title: "Initial", note: "Retained" } }}
      onSubmit={submit} fields={[
        { name: "source", label: "Source", prefill: (value) => ({ "details.title": value }) },
        ...details,
      ]}>{(form) => <button type="button" onClick={() => form.setValue("details.note", "Updated")}>Update note</button>}</FormHarness>);

    const title = await screen.findByRole("textbox", { name: "Title" }) as HTMLInputElement;
    fireEvent.change(screen.getByRole("textbox", { name: "Source" }), { target: { value: "Seeded" } });
    await waitFor(() => expect(title.value).toBe("Seeded"));
    expect((screen.getByRole("textbox", { name: "Note" }) as HTMLInputElement).value).toBe("Retained");
    fireEvent.click(screen.getByRole("button", { name: "Update note" }));
    await waitFor(() => expect((screen.getByRole("textbox", { name: "Note" }) as HTMLInputElement).value).toBe("Updated"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit.mock.calls[0]?.[0]).toEqual({ source: "Seeded", details: { title: "Seeded", note: "Updated" } }));
  });

  test("uses current form values for readOnlyWhen and locks controls during submission", async () => {
    let complete!: () => void;
    const submit = vi.fn(() => new Promise<void>((resolve) => { complete = resolve; }));
    render(<FormHarness defaultValues={{ mode: "locked", title: "Value" }} onSubmit={submit} fields={[
      { name: "mode", label: "Mode" },
      { name: "title", label: "Title", readOnlyWhen: (values) => values.mode === "locked" },
    ]} />);

    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "Mode" }), { target: { value: "editable" } });
    expect(await screen.findByRole("textbox", { name: "Title" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    expect(screen.getAllByRole("textbox").every((input) => (input as HTMLInputElement).readOnly)).toBe(true);
    complete();
    expect(await screen.findByRole("textbox", { name: "Title" })).toBeTruthy();
  });
});
