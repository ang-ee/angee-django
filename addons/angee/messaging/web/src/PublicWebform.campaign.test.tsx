// @vitest-environment happy-dom

import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { PublicWebform } from "./PublicWebform";

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (init?.method === "POST") {
      return Response.json({ submission_id: "campaign-receipt" }, { status: 202 });
    }
    return Response.json({
      slug: "campaign-form",
      title: "Campaign form",
      schema_version: 1,
      honeypot_field: "_website",
      form_schema: {
        type: "object",
        required: ["summary"],
        properties: { summary: { type: "string", label: "Summary" } },
      },
    });
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test.each([true, false])("embedded form honours showReceipt=%s before and after submission", async (showReceipt) => {
  const { container } = render(
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <PublicWebform slug="campaign-form" showReceipt={showReceipt} headingLevel="h2" />
    </AppRuntimeProvider>,
  );
  fireEvent.change(await screen.findByLabelText("Summary"), { target: { value: "A response" } });
  expect(screen.getByRole("heading", { level: 2, name: "Campaign form" })).toBeTruthy();
  expect(container.querySelector("main")).toBeNull();
  expect(screen.queryByText("Complete the fields below and keep the receipt for your records.") !== null)
    .toBe(showReceipt);
  if (!showReceipt) expect(screen.getByText("Complete the fields below.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  await screen.findByRole("heading", { level: 2, name: "Thank you" });
  expect(screen.queryByTestId("webform-receipt")?.textContent ?? null)
    .toBe(showReceipt ? "campaign-receipt" : null);
  expect(screen.getByText(showReceipt ? "Your response was received. Your receipt is:" : "Your response was received."))
    .toBeTruthy();
});

test("two embedded forms give their honeypots distinct label targets", async () => {
  const { container } = render(
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <PublicWebform slug="campaign-form" />
      <PublicWebform slug="campaign-form" />
    </AppRuntimeProvider>,
  );
  await waitFor(() => expect(container.querySelectorAll('input[name="_website"]').length).toBe(2));
  const inputs = Array.from(container.querySelectorAll<HTMLInputElement>('input[name="_website"]'));
  expect(new Set(inputs.map((input) => input.id)).size).toBe(2);
  for (const input of inputs) {
    expect(input.id).not.toBe("");
    expect(input.labels?.length).toBe(1);
    expect(input.labels?.[0]?.textContent).toBe("Website");
    expect(input.tabIndex).toBe(-1);
  }
});
