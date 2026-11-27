// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { QueryClient } from "@tanstack/react-query";
import * as React from "react";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider, containersFromChildren } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import type { FieldDescriptor } from "../page";
import type { BoundDescriptorField } from "./BoundDescriptorField";
import { FORM_CONTAINERS, FormView } from "./FormView";

// The record form's edit basis and the errors a re-read keeps, on an ordinary record form.
type Surface = Parameters<typeof BoundDescriptorField>[0]["form"];
type Commit = { shown: Row | null; basis: Row | null; dirty: boolean };

const base = { readable: true, aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false };
const scalar = (name: string, type: string) => ({ ...base, name, kind: "scalar" as const, scalar: type });
const PAGE: FieldDescriptor[] = [{ name: "title", title: true }, { name: "note", label: "Note" },
  { name: "config.root", label: "Root" }, { name: "config.other", label: "Other" }];
const NOTE: Row = { id: "note-1", title: "First", note: "n", revision: 1, config: { root: "/r", other: "o" },
  permissions: ["read", "write"] };
const refused = (errors: Record<string, string[]>) => ({ graphQLErrors: [{ message: "Validation failed",
  extensions: { code: "VALIDATION", validationErrors: errors, formErrors: [] } }] });

/** Each committed render's record shown, edit basis and dirtiness, as the form's own surface holds them. */
function Recorder({ form, commits }: { form: Surface; commits: Commit[] }) {
  React.useLayoutEffect(() => {
    commits.push({ shown: form.displayRecord, basis: form.editBasis, dirty: form.formIsDirty });
  });
  return null;
}

function fixture(refusal: Record<string, string[]> | null = null) {
  const server = { row: { ...NOTE } as Row };
  const commits: Commit[] = [];
  const getOne = vi.fn(async () => ({ data: server.row }));
  const update = vi.fn(async ({ variables }: { variables: Row }) => {
    if (refusal) throw refused(refusal);
    server.row = { ...server.row, ...variables };
    return { data: server.row };
  });
  const resource = testDataResource("notes.Note", {
    fields: [scalar("title", "String"), scalar("note", "String"), scalar("config", "JSON"), scalar("revision", "Int")],
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const { Provider, clearClients } = createUiTestProviders({ resources: [resource], apiUrl: "test://basis", router: true });
  render(<Provider dataProvider={{ getOne, update }} queryClient={queryClient}><ModalsHost><ToastProvider>
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets, containers: containersFromChildren(FORM_CONTAINERS, {}) }}>
      <FormView resource="notes.Note" id="note-1" fields={PAGE}
        formExtras={({ form }: { form: Surface }) => <Recorder form={form} commits={commits} />} />
    </AppRuntimeProvider>
  </ToastProvider></ModalsHost></Provider>);
  // Another writer stores `change`; the form reads the record again.
  const reread = async (change: Row) => {
    server.row = { ...server.row, ...change };
    await act(async () => { await queryClient.invalidateQueries(); });
    await settle();
  };
  return { server, commits, update, reread, clearClients };
}

const settle = () => new Promise((done) => setTimeout(done, 60));
const save = () => fireEvent.click(screen.getByRole("button", { name: "Save" }));
const write = (label: string, value: string) => fireEvent.change(screen.getByRole("textbox", { name: label }), { target: { value } });
const shows = (text: string) => screen.queryAllByText(text).length;
afterEach(cleanup);

test("a clean form's edit basis is the record it shows in every committed render, re-read after re-read", async () => {
  const f = fixture();

  await screen.findByRole("textbox", { name: "Note" });
  await f.reread({ title: "A", revision: 2 });
  await f.reread({ title: "B", revision: 3 });
  expect(f.commits.filter((commit) => !commit.dirty && commit.shown !== null && commit.basis !== commit.shown)).toEqual([]);
  expect(f.commits.at(-1)?.basis?.revision).toBe(3);
  f.clearClients();
});

test("a re-read that changes a clean field takes away the server's error about that field's old value", async () => {
  const f = fixture({ title: ["Title taken."] });

  await screen.findByRole("textbox", { name: "Note" });
  write("Note", "x");
  save();
  await waitFor(() => expect(shows("Title taken.")).toBe(1));
  // A re-read that leaves the title alone keeps what the save was told about it.
  await f.reread({ revision: 2 });
  expect(shows("Title taken.")).toBe(1);
  await f.reread({ title: "Renamed elsewhere", revision: 3 });
  expect(shows("Title taken.")).toBe(0);
  f.clearClients();
});

test("a re-read that changes one member of a field another member of which is dirty takes that member's error away", async () => {
  const f = fixture({ "config.root": ["Root invalid."] });

  await screen.findByRole("textbox", { name: "Other" });
  write("Other", "o2");
  save();
  await waitFor(() => expect(shows("Root invalid.")).toBe(1));
  await f.reread({ config: { root: "/fixed-elsewhere", other: "o" }, revision: 2 });
  expect((screen.getByRole("textbox", { name: "Root" }) as HTMLInputElement).value).toBe("/fixed-elsewhere");
  expect((screen.getByRole("textbox", { name: "Other" }) as HTMLInputElement).value).toBe("o2");
  expect(shows("Root invalid.")).toBe(0);
  f.clearClients();
});

test("a re-read that stores the person's own value leaves no draft, and the refusal goes with it", async () => {
  const f = fixture({ note: ["Refused."] });

  await screen.findByRole("textbox", { name: "Note" });
  write("Note", "x");
  save();
  await waitFor(() => expect(shows("Refused.")).toBe(1));
  await f.reread({ title: "Elsewhere", revision: 2 });
  expect(shows("Refused.")).toBe(1);
  // Another writer stores what the person typed: nothing is left to save, and nothing to refuse.
  await f.reread({ note: "x", revision: 3 });
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect(shows("Refused.")).toBe(0);
  f.clearClients();
});
