// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { FilePickerButton } from "./file-picker";
import { AppRuntimeProvider } from "../runtime";

afterEach(cleanup);

describe("FilePickerButton", () => {
  test("opens the hidden input and emits the chosen files once", () => {
    const onFiles = vi.fn();
    render(<FilePickerButton onFiles={onFiles} label="Choose files">Upload</FilePickerButton>);
    const input = screen.getByLabelText("Choose files") as HTMLInputElement;
    const click = vi.spyOn(input, "click");
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(click).toHaveBeenCalledOnce();
    const file = new File(["x"], "a.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(onFiles).toHaveBeenCalledWith([file]);
  });

  test("view-as disables the verb and the input", () => {
    render(<AppRuntimeProvider runtime={{ auth: { user: null, status: "authenticated", hasRole: () => false,
      viewAs: { viewAs: { userId: "person" }, currentUser: null, realUser: null, viewablePeople: [], enter: vi.fn(), exit: vi.fn() },
    } }}><FilePickerButton onFiles={vi.fn()} label="Choose files">Upload</FilePickerButton></AppRuntimeProvider>);
    expect((screen.getByRole("button", { name: "Upload" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Choose files") as HTMLInputElement).disabled).toBe(true);
  });
});
