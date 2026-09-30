// @vitest-environment happy-dom

import { useState, type ReactElement, type ReactNode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { EditorView } from "@codemirror/view";
import { FormProvider, useForm, type DefaultValues, type UseFormReturn } from "react-hook-form";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import { ToastProvider } from "../../feedback";
import { defaultWidgets, type WidgetDefinition } from "../../widgets";
import { RelationPicker } from "../relation/RelationPicker";
import { DescriptorFieldList, type DescriptorField } from "./DescriptorFieldList";
import { deserializeFormSpec } from "./form-spec";
import { useActionForm } from "./use-action-form";
import { ActionFormProvider } from "./ActionFormProvider";

type Values = Record<string, unknown>;

function FormHarness<TValues extends Values = Values>({
  fields,
  defaultValues,
  children,
  readOnly,
  onSubmit = () => undefined,
}: {
  fields: readonly DescriptorField[];
  defaultValues: DefaultValues<TValues>;
  children?: (form: UseFormReturn<TValues>) => ReactNode;
  readOnly?: boolean;
  onSubmit?: (values: TValues) => void | Promise<void>;
}): ReactElement {
  const form = useForm<TValues>({ defaultValues });
  return <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <FormProvider {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        <DescriptorFieldList fields={fields} readOnly={readOnly} />
        {children?.(form)}
        <button type="submit">Save</button>
      </form>
    </FormProvider>
  </AppRuntimeProvider>;
}

afterEach(cleanup);

describe("DescriptorFieldList", () => {
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
        <FormProvider {...form}><DescriptorFieldList fields={[
          { name: "model", label: "Model" }, { name: "condition", widget: "sibling" },
        ]} /></FormProvider>
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
    await waitFor(() => expect(submit).toHaveBeenCalledWith(values("Updated")));
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
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ payload: nextMode === "text" ? "Updated" : { note: "Retained" } }));
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

  test("renders resolved descriptors without evaluating their declaration again", () => {
    const resolve = vi.fn(() => ({ name: "title", label: "Declared title" }));
    function ResolvedForm(): ReactElement {
      const form = useForm({ defaultValues: { title: "Retained" } });
      return <FormProvider {...form}>
        <DescriptorFieldList resolvedFields={[{ name: "title", label: "Resolved title", resolve }]} />
      </FormProvider>;
    }
    render(<ResolvedForm />);
    expect((screen.getByRole("textbox", { name: "Resolved title" }) as HTMLInputElement).value).toBe("Retained");
    expect(resolve).not.toHaveBeenCalled();
  });

  test("resolves visibility and read-only presentation while preserving declared names and hidden values", async () => {
    const submit = vi.fn();
    render(<FormHarness defaultValues={{ mode: "fixed", detail: "Original", token: "retained" }} onSubmit={submit}
      fields={[
        { name: "mode", label: "Mode" },
        { name: "detail", label: "Detail", showWhen: (values) => values.mode !== "hidden",
          resolve: (values) => ({ name: "ignored", readOnly: values.mode === "fixed" }) },
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

  test("shares sibling values with custom controls and applies prefills in the same form", async () => {
    const submit = vi.fn();
    render(<FormHarness defaultValues={{ source: "", title: "", note: "" }} onSubmit={submit} fields={[
      { name: "source", label: "Source", prefill: (value) => ({ title: value }),
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
    expect((screen.getByRole("textbox", { name: "Title" }) as HTMLInputElement).value).toBe("Seeded");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit.mock.calls[0]?.[0]).toEqual({ source: "Seeded", title: "Seeded", note: "Sibling" }));
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
    const source = screen.getByRole("button", { name: "Source" });
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
