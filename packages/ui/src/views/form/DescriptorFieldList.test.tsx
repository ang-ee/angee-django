// @vitest-environment happy-dom

import type { ReactElement, ReactNode } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FormProvider, useForm, type DefaultValues, type UseFormReturn } from "react-hook-form";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import { DescriptorFieldList, type DescriptorField } from "./DescriptorFieldList";
import { deserializeFormSpec } from "./form-spec";

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
    expect(title.getAttribute("aria-describedby")?.split(" ")).toContain(error.id);
    fireEvent.click(screen.getByRole("button", { name: "Focus field" }));
    await waitFor(() => expect(document.activeElement).toBe(title));
    fireEvent.blur(title);
    await waitFor(() => expect(screen.getByLabelText("Touched").textContent).toBe("true"));
    fireEvent.change(title, { target: { value: "Updated" } });
    await waitFor(() => expect(screen.queryByText("Choose a title")).toBeNull());
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
    expect(screen.queryByRole("textbox")).toBeNull();
    complete();
    expect(await screen.findByRole("textbox", { name: "Title" })).toBeTruthy();
  });
});
