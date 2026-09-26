// @vitest-environment happy-dom

import type {
  DataResourceMetadata,
  Row,
  SchemaFieldMetadata,
} from "@angee/metadata";
import {
  ModelMetadataProvider,
  schemaFieldMetadataFromDataResources,
} from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  RouterContextProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { useState, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import { ActionFormDialog, serializeActionArgValues } from "./ActionFormDialog";
import type { ActionArg, ActionDescriptor, ActionFormContext } from "../page";

// cmdk scrolls the active option into view; happy-dom has no layout engine.
Element.prototype.scrollIntoView = vi.fn();

const listRows = vi.hoisted(() => ({
  collections: [
    { id: "col-primary", name: "Primary Collection" },
    { id: "col-secondary", name: "Secondary Collection" },
  ] as Row[],
  documents: [
    { id: "doc-1", number: "DOC-1" },
    { id: "doc-2", number: "DOC-2" },
  ] as Row[],
}));

const listOptions = vi.hoisted(() => [] as unknown[]);

function scalarField(name: string, scalar: string) {
  return {
    name,
    kind: "scalar" as const,
    scalar,
    readable: true,
    filterable: true,
    sortable: true,
    aggregatable: false,
    groupable: false,
    creatable: false,
    updatable: false,
    requiredOnCreate: false,
  };
}

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useInvalidate: () => vi.fn(async () => undefined),
    useOne: () => ({
      result: undefined,
      query: { isFetching: false, error: null },
    }),
    useList: (options?: {
      resource?: string;
      filters?: readonly unknown[];
      queryOptions?: { enabled?: boolean };
    }) => {
      listOptions.push(options);
      const enabled = options?.queryOptions?.enabled !== false;
      const rows =
        options?.resource === "collections"
          ? listRows.collections
          : options?.resource === "documents"
            ? listRows.documents
            : [];
      return {
        result: enabled
          ? { data: rows, total: rows.length }
          : { data: [], total: 0 },
        query: { isFetching: false, error: null, refetch: vi.fn() },
      };
    },
  };
});

function resourceMetadata(
  typeName: string,
  modelLabel: string,
  listRoot: string,
  representation: string,
): DataResourceMetadata {
  return testDataResource(modelLabel, {
    modelName: modelLabel,
    roots: { list: listRoot },
    typeNames: { node: typeName },
    recordRepresentation: representation,
    fields: [scalarField("id", "ID"), scalarField(representation, "String")],
    capabilities: ["list"],
  });
}

const metadata: SchemaFieldMetadata = schemaFieldMetadataFromDataResources([
  resourceMetadata("CollectionType", "Collection", "collections", "name"),
  resourceMetadata(
      "DocumentType",
      "Document",
      "documents",
      "number",
  ),
]);

const registerReviewArgs: readonly ActionArg[] = [
  {
    name: "documentIds",
    argKind: "relationList",
    resource: "Document",
    label: "Documents",
    filters: [{ field: "status", operator: "eq", value: "submitted" }],
  },
  {
    name: "collection",
    argKind: "relation",
    resource: "Collection",
    label: "Collection",
    filters: [{ field: "kind", operator: "eq", value: "primary" }],
  },
  {
    name: "date",
    widget: "text",
    label: "Date",
    defaultValue: "2026-07-05",
  },
  { name: "amount", widget: "text", label: "Amount", optional: true },
];

function registerReviewAction(
  submit: ActionDescriptor["submit"],
): ActionDescriptor {
  return {
    id: "register-review",
    label: "Register review",
    args: registerReviewArgs,
    submit,
  };
}

const context: ActionFormContext = {
  record: { id: "doc-1", amount_total: "1234.56", collection: { id: "col-secondary", name: "Secondary Collection" } },
  selectedIds: ["doc-1", "doc-2"],
};

function Harness({ action, onSucceeded }: {
  action: ActionDescriptor;
  onSucceeded?: (outcome: { ok: boolean; message: string; id?: string }) => void;
}): ReactElement {
  const [open, setOpen] = useState(true);
  return (
    <ActionFormDialog
      action={action}
      context={context}
      open={open}
      onSucceeded={onSucceeded}
      onOpenChange={setOpen}
    />
  );
}

/** Open the collection relation picker and select an option by its label. */
async function pickCollection(label: string): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "Collection" }));
  fireEvent.click(await screen.findByText(label));
}

function renderDialog(
  action: ActionDescriptor,
  onSucceeded?: (outcome: { ok: boolean; message: string; id?: string }) => void,
  onParentSubmit?: () => void,
): void {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const dialog = <Harness action={action} onSucceeded={onSucceeded} />;
  render(
    <RouterContextProvider router={router}>
      <ModalsHost>
        <ToastProvider>
          <ModelMetadataProvider metadata={metadata}>
            <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
              {onParentSubmit ? <form onSubmit={(event) => {
                event.preventDefault();
                onParentSubmit();
              }}>{dialog}</form> : dialog}
            </AppRuntimeProvider>
          </ModelMetadataProvider>
        </ToastProvider>
      </ModalsHost>
    </RouterContextProvider>,
  );
}

describe("serializeActionArgValues", () => {
  const args: readonly ActionArg[] = [
    { name: "documentIds", argKind: "relationList", resource: "Document" },
  ];

  test("normalizes mixed relation values to ordered unique string ids without mutating the draft", () => {
    const documentIds = Object.freeze([
      "doc-2", { id: "doc-1", number: "DOC-1" }, 7, { id: 7 }, "doc-2", 0,
      "", null, undefined, false, {}, { id: "" }, { id: false }, ["doc-3"],
    ]);
    const values = Object.freeze({ documentIds });

    expect(serializeActionArgValues(args, values)).toEqual({
      documentIds: ["doc-2", "doc-1", "7", "0"],
    });
  });

  test.each([
    { documentIds: undefined }, { documentIds: null }, { documentIds: "doc-1" },
    { documentIds: 7 }, { documentIds: { id: "doc-1" } }, { documentIds: [] },
  ])(
    "submits an empty relation list for %j",
    ({ documentIds }) => {
      expect(serializeActionArgValues(args, { documentIds })).toEqual({ documentIds: [] });
    },
  );

  test("preserves non-list arguments and undeclared values", () => {
    const values = {
      documentIds: ["doc-1", "doc-1"],
      tags: ["same", "same", ""],
      collection: { id: "col-primary" },
      amount: "001.00",
      extra: [1, 1],
    };

    expect(serializeActionArgValues([
      ...args,
      { name: "tags", argKind: "scalar", widget: "tagInput" },
      { name: "collection", argKind: "relation", resource: "Collection" },
      { name: "amount", widget: "text" },
    ], values)).toEqual({ ...values, documentIds: ["doc-1"] });
  });
});

describe("ActionFormDialog", () => {
  test("passes normalized relation-list values to a custom submit", async () => {
    const submit = vi.fn().mockResolvedValue({ ok: true, message: "Done." });
    renderDialog({
      id: "collect",
      label: "Collect",
      args: [{
        name: "documentIds", argKind: "relationList", resource: "Document",
        fromContext: () => ["doc-2", "doc-1", "doc-2"],
      }],
      submit,
    });

    fireEvent.click(screen.getByRole("button", { name: "Collect" }));

    await waitFor(() => expect(submit).toHaveBeenCalledWith(
      { documentIds: ["doc-2", "doc-1"] }, context,
    ));
  });

  test("does not submit its parent record form through the dialog portal", async () => {
    const submit = vi.fn().mockResolvedValue({ ok: false, message: "Fix the amount." });
    const parentSubmit = vi.fn();
    renderDialog({
      id: "collect",
      label: "Collect",
      submit,
      args: [{ name: "amount", widget: "text", label: "Amount" }],
    }, undefined, parentSubmit);

    fireEvent.change(screen.getByRole("textbox", { name: "Amount" }), {
      target: { value: "500" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Collect" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(parentSubmit).not.toHaveBeenCalled();
  });

  test("prefills a saved relation from record context ahead of the fallback default", async () => {
    const submit = vi.fn().mockResolvedValue({ ok: true });
    renderDialog({
      id: "collect", label: "Collect", submit,
      args: [{
        name: "collection", argKind: "relation", resource: "Collection", label: "Collection",
        defaultValue: "col-primary", fromContext: ({ record }) => record?.collection,
      }],
    });
    fireEvent.click(screen.getByRole("button", { name: "Collect" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ collection: "col-secondary" }, context));
  });

  test("prefills scalar args from the invoking record and submits user edits", async () => {
    const outcome = { ok: true, message: "Saved.", id: "result-1" };
    const submit = vi.fn().mockResolvedValue(outcome);
    const onSucceeded = vi.fn();
    renderDialog({
      id: "collect", label: "Collect", submit,
      args: [
        { name: "amount", widget: "text", label: "Amount", fromContext: ({ record }) => record?.amount_total },
        { name: "zero", widget: "text", label: "Zero", defaultValue: "99", fromContext: () => 0 },
        { name: "fallback", widget: "text", label: "Fallback", defaultValue: "Default", fromContext: () => undefined },
      ],
    }, onSucceeded);
    expect((screen.getByLabelText("Amount") as HTMLInputElement).value).toBe("1234.56");
    expect((screen.getByLabelText("Zero") as HTMLInputElement).value).toBe("0");
    expect((screen.getByLabelText("Fallback") as HTMLInputElement).value).toBe("Default");
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "500.00" } });
    fireEvent.click(screen.getByRole("button", { name: "Collect" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith(
      { amount: "500.00", zero: 0, fallback: "Default" }, context,
    ));
    expect(onSucceeded).toHaveBeenCalledWith(outcome);
  });

  test("serializes datetime args with the picked local UTC offset", async () => {
    const submit = vi.fn().mockResolvedValue({ ok: true, message: "Snoozed." });
    renderDialog({
      id: "snooze",
      label: "Snooze",
      args: [
        {
          name: "until",
          label: "Until",
          kind: "datetime",
          defaultValue: "2026-08-31T00:00",
        },
      ],
      submit,
    });

    fireEvent.click(screen.getByRole("button", { name: "Snooze" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]?.[0]).toEqual({
      until: expect.stringMatching(
        /^2026-08-31T00:00:00[+-]\d{2}:\d{2}$/,
      ),
    });
  });

  afterEach(() => cleanup());
  beforeEach(() => {
    vi.clearAllMocks();
    listOptions.length = 0;
  });

  test("prefills the relation list from context and renders every arg", async () => {
    renderDialog(registerReviewAction(vi.fn()));

    // The relation list is seeded from the invoking selection (labels from options).
    expect(await screen.findByText("DOC-1")).toBeTruthy();
    expect(screen.getByText("DOC-2")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove DOC-1" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove DOC-2" })).toBeTruthy();
    // The single relation composes the relation picker.
    expect(screen.getByRole("button", { name: "Collection" })).toBeTruthy();
    // The scalars render editable inputs.
    expect(screen.getByRole("textbox", { name: "Date" })).toBeTruthy();
    expect(screen.getByRole<HTMLInputElement>("textbox", { name: "Date" }).value)
      .toBe("2026-07-05");
    expect(screen.getByRole("textbox", { name: "Amount" })).toBeTruthy();
  });

  test("forwards a relation argument's declared filters to its option query", async () => {
    renderDialog(registerReviewAction(vi.fn()));

    await pickCollection("Primary Collection");

    expect(listOptions).toContainEqual(
      expect.objectContaining({
        resource: "collections",
        filters: [{ field: "kind", operator: "eq", value: "primary" }],
      }),
    );
  });

  test("forwards a relation-list argument's declared filters to its option query", async () => {
    renderDialog(registerReviewAction(vi.fn()));

    await screen.findByText("DOC-1");

    expect(listOptions).toContainEqual(
      expect.objectContaining({
        resource: "documents",
        filters: [{ field: "status", operator: "eq", value: "submitted" }],
      }),
    );
  });

  test("binds an in-band field error, stays open, then closes and toasts on success", async () => {
    const submit = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        message: "Fix the amount.",
        validationErrors: { amount: ["Amount exceeds the limit."] },
      })
      .mockResolvedValueOnce({ ok: true, message: "Review registered." });
    renderDialog(registerReviewAction(submit));

    await screen.findByText("DOC-1");
    await pickCollection("Secondary Collection");
    fireEvent.change(screen.getByRole("textbox", { name: "Date" }), {
      target: { value: "2026-07-05" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Amount" }), {
      target: { value: "500" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Register review" }));

    // The relation pick + context selection reach the mutation as typed variables.
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]?.[0]).toMatchObject({
      documentIds: ["doc-1", "doc-2"],
      collection: "col-secondary",
      date: "2026-07-05",
      amount: "500",
    });

    // The domain failure binds inline and the dialog stays open.
    expect(await screen.findByText("Amount exceeds the limit.")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Register review" }),
    ).toBeTruthy();

    // Editing the flagged field clears its bound error, and a second submit succeeds.
    fireEvent.change(screen.getByRole("textbox", { name: "Amount" }), {
      target: { value: "250" },
    });
    expect(screen.queryByText("Amount exceeds the limit.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Register review" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
    // Success toasts the message and closes the dialog.
    expect(await screen.findByText("Review registered.")).toBeTruthy();
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Register review" }),
      ).toBeNull(),
    );
  });

  test("an explicit relation-list edit wins over the context prefill", async () => {
    const submit = vi.fn().mockResolvedValue({ ok: true, message: "Done." });
    renderDialog(registerReviewAction(submit));

    // Full forms keep explicit per-chip removal.
    fireEvent.click(await screen.findByRole("button", { name: "Remove DOC-1" }));
    await pickCollection("Secondary Collection");
    fireEvent.change(screen.getByRole("textbox", { name: "Date" }), {
      target: { value: "2026-07-05" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Register review" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]?.[0]).toMatchObject({ documentIds: ["doc-2"] });
  });
});
