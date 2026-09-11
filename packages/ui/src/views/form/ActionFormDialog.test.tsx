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
import { ActionFormDialog } from "./ActionFormDialog";
import type { ActionArg, ActionDescriptor, ActionFormContext } from "../page";

// cmdk scrolls the active option into view; happy-dom has no layout engine.
Element.prototype.scrollIntoView = vi.fn();

const listRows = vi.hoisted(() => ({
  journals: [
    { id: "jnl-bank", name: "Bank Journal" },
    { id: "jnl-cash", name: "Cash Journal" },
  ] as Row[],
  invoices: [
    { id: "inv-1", number: "INV-1" },
    { id: "inv-2", number: "INV-2" },
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
        options?.resource === "journals"
          ? listRows.journals
          : options?.resource === "invoices"
            ? listRows.invoices
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
  resourceMetadata("JournalType", "Journal", "journals", "name"),
  resourceMetadata(
      "InvoiceType",
      "Invoice",
      "invoices",
      "number",
  ),
]);

const registerPaymentArgs: readonly ActionArg[] = [
  {
    name: "invoiceIds",
    argKind: "relationList",
    resource: "Invoice",
    label: "Invoices",
    filters: [{ field: "status", operator: "eq", value: "submitted" }],
  },
  {
    name: "journal",
    argKind: "relation",
    resource: "Journal",
    label: "Journal",
    filters: [{ field: "kind", operator: "eq", value: "bank" }],
  },
  {
    name: "date",
    widget: "text",
    label: "Date",
    defaultValue: "2026-07-05",
  },
  { name: "amount", widget: "text", label: "Amount", optional: true },
];

function registerPaymentAction(
  submit: ActionDescriptor["submit"],
): ActionDescriptor {
  return {
    id: "register-payment",
    label: "Register payment",
    args: registerPaymentArgs,
    submit,
  };
}

const context: ActionFormContext = {
  record: { id: "inv-1", amount_total: "1234.56" },
  selectedIds: ["inv-1", "inv-2"],
};

function Harness({ action }: { action: ActionDescriptor }): ReactElement {
  const [open, setOpen] = useState(true);
  return (
    <ActionFormDialog
      action={action}
      context={context}
      open={open}
      onOpenChange={setOpen}
    />
  );
}

/** Open the journal relation picker and select an option by its label. */
async function pickJournal(label: string): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "Journal" }));
  fireEvent.click(await screen.findByText(label));
}

function renderDialog(action: ActionDescriptor): void {
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
  render(
    <RouterContextProvider router={router}>
      <ModalsHost>
        <ToastProvider>
          <ModelMetadataProvider metadata={metadata}>
            <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
              <Harness action={action} />
            </AppRuntimeProvider>
          </ModelMetadataProvider>
        </ToastProvider>
      </ModalsHost>
    </RouterContextProvider>,
  );
}

describe("ActionFormDialog", () => {
  test("prefills scalar args from the invoking record and submits user edits", async () => {
    const submit = vi.fn().mockResolvedValue({ ok: true, message: "Saved." });
    renderDialog({
      id: "collect", label: "Collect", submit,
      args: [
        { name: "amount", widget: "text", label: "Amount", fromContext: ({ record }) => record?.amount_total },
        { name: "zero", widget: "text", label: "Zero", defaultValue: "99", fromContext: () => 0 },
        { name: "fallback", widget: "text", label: "Fallback", defaultValue: "Default", fromContext: () => undefined },
      ],
    });
    expect((screen.getByLabelText("Amount") as HTMLInputElement).value).toBe("1234.56");
    expect((screen.getByLabelText("Zero") as HTMLInputElement).value).toBe("0");
    expect((screen.getByLabelText("Fallback") as HTMLInputElement).value).toBe("Default");
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "500.00" } });
    fireEvent.click(screen.getByRole("button", { name: "Collect" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith(
      { amount: "500.00", zero: 0, fallback: "Default" }, context,
    ));
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
    renderDialog(registerPaymentAction(vi.fn()));

    // The relation list is seeded from the invoking selection (labels from options).
    expect(await screen.findByText("INV-1")).toBeTruthy();
    expect(screen.getByText("INV-2")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove INV-1" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove INV-2" })).toBeTruthy();
    // The single relation composes the relation picker.
    expect(screen.getByRole("button", { name: "Journal" })).toBeTruthy();
    // The scalars render editable inputs.
    expect(screen.getByRole("textbox", { name: "Date" })).toBeTruthy();
    expect(screen.getByRole<HTMLInputElement>("textbox", { name: "Date" }).value)
      .toBe("2026-07-05");
    expect(screen.getByRole("textbox", { name: "Amount" })).toBeTruthy();
  });

  test("forwards a relation argument's declared filters to its option query", async () => {
    renderDialog(registerPaymentAction(vi.fn()));

    await pickJournal("Bank Journal");

    expect(listOptions).toContainEqual(
      expect.objectContaining({
        resource: "journals",
        filters: [{ field: "kind", operator: "eq", value: "bank" }],
      }),
    );
  });

  test("forwards a relation-list argument's declared filters to its option query", async () => {
    renderDialog(registerPaymentAction(vi.fn()));

    await screen.findByText("INV-1");

    expect(listOptions).toContainEqual(
      expect.objectContaining({
        resource: "invoices",
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
        validationErrors: { amount: ["Amount exceeds the balance."] },
      })
      .mockResolvedValueOnce({ ok: true, message: "Payment registered." });
    renderDialog(registerPaymentAction(submit));

    await screen.findByText("INV-1");
    await pickJournal("Cash Journal");
    fireEvent.change(screen.getByRole("textbox", { name: "Date" }), {
      target: { value: "2026-07-05" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Amount" }), {
      target: { value: "500" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Register payment" }));

    // The relation pick + context selection reach the mutation as typed variables.
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]?.[0]).toMatchObject({
      invoiceIds: ["inv-1", "inv-2"],
      journal: "jnl-cash",
      date: "2026-07-05",
      amount: "500",
    });

    // The domain failure binds inline and the dialog stays open.
    expect(await screen.findByText("Amount exceeds the balance.")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Register payment" }),
    ).toBeTruthy();

    // Editing the flagged field clears its bound error, and a second submit succeeds.
    fireEvent.change(screen.getByRole("textbox", { name: "Amount" }), {
      target: { value: "250" },
    });
    expect(screen.queryByText("Amount exceeds the balance.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Register payment" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
    // Success toasts the message and closes the dialog.
    expect(await screen.findByText("Payment registered.")).toBeTruthy();
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Register payment" }),
      ).toBeNull(),
    );
  });

  test("an explicit relation-list edit wins over the context prefill", async () => {
    const submit = vi.fn().mockResolvedValue({ ok: true, message: "Done." });
    renderDialog(registerPaymentAction(submit));

    // Full forms keep explicit per-chip removal.
    fireEvent.click(await screen.findByRole("button", { name: "Remove INV-1" }));
    await pickJournal("Cash Journal");
    fireEvent.change(screen.getByRole("textbox", { name: "Date" }), {
      target: { value: "2026-07-05" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Register payment" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]?.[0]).toMatchObject({ invoiceIds: ["inv-2"] });
  });
});
