// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { RelationField } from "./RelationField";

afterEach(cleanup);

const OPTIONS = [{ value: "a", label: "Alpha" }, { value: "b", label: "Beta" }];

test.each(["escape", "selection"])("closing by %s resets the remote filter with the visible search", async (close) => {
  const changed = vi.fn();
  function RemotePicker() {
    const [search, setSearch] = useState("");
    return <><output data-testid="remote-search">{search}</output><RelationField aria-label="Related row"
      options={OPTIONS.filter((option) => option.label.includes(search))}
      onSearchChange={(next) => { changed(next); setSearch(next); }} /></>;
  }
  render(<RemotePicker />);
  const trigger = screen.getByRole("combobox", { name: "Related row" });
  fireEvent.click(trigger);
  fireEvent.change(await screen.findByPlaceholderText("Search…"), { target: { value: "Beta" } });
  expect(screen.getByTestId("remote-search").textContent).toBe("Beta");
  expect(screen.queryByRole("option", { name: "Alpha" })).toBeNull();
  if (close === "escape") fireEvent.keyDown(screen.getByPlaceholderText("Search…"), { key: "Escape" });
  else fireEvent.click(screen.getByRole("option", { name: "Beta" }));
  await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
  expect(changed).toHaveBeenLastCalledWith("");
  expect(screen.getByTestId("remote-search").textContent).toBe("");
  fireEvent.click(trigger);
  expect(await screen.findByRole("option", { name: "Alpha" })).toBeTruthy();
  expect(screen.getByPlaceholderText("Search…")).toHaveProperty("value", "");
});
