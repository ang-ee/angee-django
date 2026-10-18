// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useMemo, useState, type ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { RecordFieldMarksProvider, RecordFieldMarkControl, usePublishActiveRecordForm, useRecordFieldMarks, useRevealedRecordField, type RecordFieldMark } from "./record-field-marks";

vi.mock("../resource/record-chrome-context", () => ({ useRecordChromeContextMaybe: () => ({ recordId: "nte_7", canonicalResource: "notes.Note" }) }));
vi.mock("../relation/RecordReference", () => ({
  RecordReference: ({ model, id, label }: { model: string; id: string; label: ReactNode }) =>
    <a href={`/${model}/${id}`}>{label}</a>,
}));
afterEach(cleanup);

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
          target: { model: "parties.PartyStatement", id: "pst_9" },
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

  render(<RecordFieldMarksProvider><Marks /></RecordFieldMarksProvider>);
  const link = await screen.findByRole("link", { name: "Source" });
  expect(link.getAttribute("href")).toBe("/parties.PartyStatement/pst_9");
  expect(screen.getByRole("button", { name: "Unconfirmed: Name" })).toBeTruthy();
  const trigger = link.parentElement!;
  fireEvent.mouseEnter(trigger);
  fireEvent.mouseMove(trigger);
  expect(await screen.findByText("Directory: Customer contacts")).toBeTruthy();
});
