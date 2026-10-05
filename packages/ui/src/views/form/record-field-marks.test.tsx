// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useMemo, useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { RecordFieldMarksProvider, RecordFieldMarkButton, usePublishActiveRecordForm, useRecordFieldMarks, useRevealedRecordField, type RecordFieldMark } from "./record-field-marks";

vi.mock("../resource/record-chrome-context", () => ({ useRecordChromeContextMaybe: () => ({ recordId: "nte_7", canonicalResource: "notes.Note" }) }));
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
    return <><RecordFieldMarkButton field="display_name" label="Name" /><output>{field?.field ?? "none"}</output></>;
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
