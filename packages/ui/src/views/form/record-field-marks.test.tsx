// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useMemo, useState, type ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";
import {
  RecordFieldMarksProvider, RecordFieldMarkControl, UnregisteredImplementationExplanation, UnregisteredImplementationField,
  usePublishActiveRecordForm, useRecordFieldMarks, useRevealedRecordField, useUnregisteredImplementationExplanation,
  type RecordFieldMark,
} from "./record-field-marks";

vi.mock("../resource/record-chrome-context", () => ({ useRecordChromeContextMaybe: () => ({ recordId: "nte_7", canonicalResource: "notes.Note" }) }));
vi.mock("../relation/RecordReference", () => ({
  RecordReference: ({ model, id, label }: { model: string; id: string; label: ReactNode }) =>
    <a href={`/${model}/${id}`}>{label}</a>,
}));
const { Provider, clearClients } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); });

test("mark and reveal updates do not render a form that only consumes stable dispatch", async () => {
  const renders = vi.fn();
  const form = { model: "notes.Note", id: "nte_7", focusField: vi.fn() };
  let publish: (marks: readonly RecordFieldMark[]) => void = () => {};
  function Form() { renders(); usePublishActiveRecordForm(form); return <input aria-label="Name" />; }
  function Marks() {
    const [marks, setMarks] = useState<readonly RecordFieldMark[]>([]); publish = setMarks;
    const publications = useMemo(() => [{ model: form.model, id: form.id, marks }], [marks]);
    useRecordFieldMarks(publications);
    const field = useRevealedRecordField();
    return <><RecordFieldMarkControl field="display_name" label="Name" /><output>{field?.field ?? "none"}</output></>;
  }
  render(<RecordFieldMarksProvider><Form /><Marks /></RecordFieldMarksProvider>);
  const before = renders.mock.calls.length;
  act(() => publish([{ field: "display_name", label: "Unconfirmed", tone: "warning", onReveal: vi.fn() }]));
  fireEvent.click(await screen.findByRole("button", { name: "Unconfirmed: Name" }));
  expect(screen.getByText("display_name")).toBeTruthy();
  expect(renders).toHaveBeenCalledTimes(before);
  act(() => publish([]));
  await waitFor(() => expect(screen.getByText("none")).toBeTruthy());
  expect(renders).toHaveBeenCalledTimes(before);
});

test("a linked mark opens its record and names its origin on hover", async () => {
  function Marks() {
    const publications = useMemo(() => [
      {
        model: "notes.Note",
        id: "nte_7",
        marks: [{
          field: "display_name",
          label: "Source",
          tone: "info" as const,
          origin: "Directory: Customer contacts",
          target: { model: "parties.Party", id: "pty_9" },
        }],
      },
      {
        model: "notes.Note",
        id: "nte_7",
        marks: [{
          field: "display_name",
          label: "Unconfirmed",
          tone: "warning" as const,
          onReveal: vi.fn(),
        }],
      },
    ], []);
    useRecordFieldMarks(publications);
    return <RecordFieldMarkControl field="display_name" label="Name" />;
  }

  render(<Provider><RecordFieldMarksProvider><Marks /></RecordFieldMarksProvider></Provider>);
  const link = await screen.findByRole("link", { name: "Source" });
  expect(link.getAttribute("href")).toBe("/parties.Party/pty_9");
  expect(screen.getByRole("button", { name: "Unconfirmed: Name" })).toBeTruthy();
  const trigger = link.parentElement!;
  fireEvent.mouseEnter(trigger);
  expect(screen.getByText("Directory: Customer contacts")).toBeTruthy();
});

test("a reveal mark preserves its origin tooltip", async () => {
  const onReveal = vi.fn();
  function Marks() {
    const publications = useMemo(() => [{ model: "notes.Note", id: "nte_7", marks: [{
      field: "display_name", label: "Source", tone: "info" as const,
      origin: "Imported directory", onReveal,
    }] }], []);
    useRecordFieldMarks(publications);
    return <RecordFieldMarkControl field="display_name" label="Name" />;
  }
  render(<Provider><RecordFieldMarksProvider><Marks /></RecordFieldMarksProvider></Provider>);
  const mark = await screen.findByRole("button", { name: "Source: Name" });
  fireEvent.mouseEnter(mark.parentElement!);
  expect(screen.getByText("Imported directory")).toBeTruthy();
  fireEvent.click(mark);
  expect(onReveal).toHaveBeenCalledOnce();
});

test("beside a field error saying the refusal, the explanation stays only where a mark is described by it", () => {
  const REFUSED = "This actor may not choose an implementation.";
  function Row() {
    const explanation = useUnregisteredImplementationExplanation("backend");
    return <><RecordFieldMarkControl field="backend" label="Backend" />
      <UnregisteredImplementationExplanation explanation={explanation} messages={[REFUSED]} /></>;
  }
  const row = (marked: boolean) => <Provider><RecordFieldMarksProvider>
    <UnregisteredImplementationField field="backend" marked={marked} readOnly refusal={REFUSED}><Row /></UnregisteredImplementationField>
  </RecordFieldMarksProvider></Provider>;

  // Marked and read-only: the mark is the keyboard's stop, described by the hidden explanation.
  const { rerender } = render(row(true));
  const mark = screen.getByRole("img", { name: "Not registered: Backend" });
  const described = document.getElementById(mark.getAttribute("aria-describedby") ?? "");
  expect(described?.textContent).toBe(REFUSED);
  expect(described?.className).toContain("sr-only");
  // No mark: nothing references it, and the row's error already says it, so nothing is rendered.
  rerender(row(false));
  expect(screen.queryByRole("img", { name: "Not registered: Backend" })).toBeNull();
  expect(screen.queryAllByText(REFUSED)).toEqual([]);
});
