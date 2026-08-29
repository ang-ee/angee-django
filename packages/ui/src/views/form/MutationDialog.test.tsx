// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { afterEach, describe, expect, test, vi } from "vitest";
import { ModelMetadataProvider } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";

import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import {
  LabeledDescriptorField,
  MutationDialog,
  emptyValueForField,
  mutationDialogValueCodecs,
} from "./MutationDialog";

const parseRawValues = (values: Readonly<Record<string, unknown>>) => values;

describe("MutationDialog", () => {
  afterEach(cleanup);

  test("associates descriptor labels and descriptions with widget inputs", () => {
    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <MutationDialog
          open
          onOpenChange={vi.fn()}
          title="Connect Telegram"
          fields={[
            {
              name: "api_hash",
              label: "API hash",
              widget: "password",
              description: "Create or copy your Telegram application keys.",
            },
          ]}
          submitLabel="Connect"
          parseValues={parseRawValues}
          onSubmit={vi.fn()}
        />
      </AppRuntimeProvider>,
    );

    const label = screen.getByText("API hash").closest("label");
    const input = screen.getByLabelText("API hash");
    const description = screen.getByText(
      "Create or copy your Telegram application keys.",
    );

    expect(label?.htmlFor).toBe(input.id);
    expect(input.id).not.toBe("");
    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(
      description.id,
    );
  });

  test("binds server messages to the matching shared field control", () => {
    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <LabeledDescriptorField
          field={{ name: "title", label: "Title" }}
          value=""
          messages={["This field is required."]}
          onChange={vi.fn()}
        />
      </AppRuntimeProvider>,
    );

    const input = screen.getByLabelText("Title");
    const message = screen.getByText("This field is required.");

    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(
      message.id,
    );
    expect(message.closest('[data-invalid=""]')).not.toBeNull();
  });

  test("uses schema-safe empty values for descriptor field kinds", () => {
    expect(emptyValueForField({ kind: "integer" })).toBeNull();
    expect(emptyValueForField({ kind: "number" })).toBeNull();
    expect(emptyValueForField({ kind: "any" })).toBeNull();
    expect(emptyValueForField({ kind: "array" })).toEqual([]);
    expect(emptyValueForField({ kind: "object" })).toEqual({});
    expect(emptyValueForField({ kind: "boolean" })).toBe(false);
    expect(emptyValueForField({ kind: "any", widget: "select" })).toBe("");
    expect(emptyValueForField({ kind: "string" })).toBe("");
  });

  test("an unknown relation degrades to a disabled control and development warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <ModelMetadataProvider
          metadata={{ types: {}, resources: [testDataResource("parties.Party")] }}
        >
          <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
            <MutationDialog
              open
              onOpenChange={vi.fn()}
              title="Assign owner"
              fields={[
                {
                  name: "owner",
                  label: "Owner",
                  relation: {
                    resource: "missing.Person",
                    labelField: "display_name",
                  },
                },
              ]}
              submitLabel="Assign"
              parseValues={parseRawValues}
              onSubmit={vi.fn()}
            />
          </AppRuntimeProvider>
        </ModelMetadataProvider>
      </QueryClientProvider>,
    );

    expect(
      (screen.getByRole("button", { name: "Owner" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(warn).toHaveBeenCalledWith(
      expect.stringMatching(/mutation dialog relation.*missing\.Person/),
    );
    warn.mockRestore();
  });

  test("decodes raw controls before submitting typed values", async () => {
    const onSubmit = vi.fn();
    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <MutationDialog
          open
          onOpenChange={vi.fn()}
          title="Create"
          fields={[
            { name: "name", label: "Name", required: true },
            { name: "note", label: "Note" },
          ]}
          submitLabel="Create"
          parseValues={(values) => ({
            name: mutationDialogValueCodecs.requiredString(values.name, "name"),
            note: mutationDialogValueCodecs.string(values.note),
          })}
          onSubmit={onSubmit}
        />
      </AppRuntimeProvider>,
    );

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "  Ada  " },
    });
    fireEvent.change(screen.getByLabelText("Note"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({ name: "Ada", note: null }),
    );
  });

  test("keeps whitespace only through the explicit verbatim-string codec", () => {
    expect(mutationDialogValueCodecs.string("  secret  ")).toBe("secret");
    expect(mutationDialogValueCodecs.string("   ")).toBeNull();
    expect(mutationDialogValueCodecs.string({ value: "secret" })).toBeNull();
    expect(() =>
      mutationDialogValueCodecs.requiredString({ value: "secret" }, "name"),
    ).toThrow('MutationDialog invariant: required field "name"');
    expect(
      mutationDialogValueCodecs.integer(
        " 4 ",
        "Count",
        (label) => `${label} must be a whole number.`,
      ),
    ).toBe(4);
    expect(mutationDialogValueCodecs.verbatimString("  secret  ", "secret")).toBe(
      "  secret  ",
    );
    expect(() => mutationDialogValueCodecs.verbatimString("", "secret")).toThrow(
      'MutationDialog invariant: required verbatim field "secret"',
    );
    expect(() =>
      mutationDialogValueCodecs.integer(
        "4.5",
        "Port",
        (label) => `${label} must be a whole number.`,
      ),
    ).toThrow("Port must be a whole number.");

    const localMidnight = mutationDialogValueCodecs.datetime(
      "2026-08-31T00:00",
    );
    expect(localMidnight).toMatch(
      /^2026-08-31T00:00:00[+-]\d{2}:\d{2}$/,
    );
    expect(mutationDialogValueCodecs.datetime("")).toBeNull();
    expect(() => mutationDialogValueCodecs.datetime("not-a-date")).toThrow(
      "datetime value was not a valid ISO-8601 date-time",
    );
  });

  test("shows owner-level busy feedback and locks both footer actions", async () => {
    let finishSubmit: (() => void) | undefined;
    const pendingSubmit = new Promise<void>((resolve) => {
      finishSubmit = resolve;
    });
    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <MutationDialog
          open
          onOpenChange={vi.fn()}
          title="Connect"
          fields={[{ name: "name", label: "Name", required: true }]}
          submitLabel="Connect"
          submittingLabel="Connecting…"
          parseValues={parseRawValues}
          onSubmit={() => pendingSubmit}
        />
      </AppRuntimeProvider>,
    );

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Ada" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: "Connecting…" })
          .getAttribute("aria-busy"),
      ).toBe("true"),
    );
    expect(
      (screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    finishSubmit?.();
    await pendingSubmit;
  });
});
