// @vitest-environment happy-dom

import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  useParams: () => ({ slug: "product-feedback" }),
}));

import { PublicWebformPage } from "./PublicWebformPage";

describe("PublicWebformPage", () => {
  afterEach(() => vi.restoreAllMocks());

  test("renders the deserialized form spec and posts answers with a stable receipt id", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      requests.push({ url: String(input), init });
      if (!init || init.method === "GET") {
        return Response.json({
          slug: "product-feedback",
          title: "Product feedback",
          schema_version: 1,
          form_schema: {
            type: "object",
            required: ["email", "problem"],
            properties: {
              email: { type: "string", widget: "email", label: "Email" },
              problem: { type: "string", widget: "textarea", label: "Problem" },
              score: { type: "integer", label: "Score" },
            },
          },
        });
      }
      const requestBody = JSON.parse(String(init.body)) as {
        submission_id: string;
        answers: Record<string, unknown>;
      };
      expect(requestBody.answers).toEqual({
        email: "ada@example.com",
        problem: "Make intake painless",
      });
      return Response.json({ submission_id: requestBody.submission_id }, { status: 202 });
    });

    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <PublicWebformPage />
      </AppRuntimeProvider>,
    );

    fireEvent.change(await screen.findByLabelText("Email"), {
      target: { value: "ada@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Problem"), {
      target: { value: "Make intake painless" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));

    await waitFor(() => expect(screen.getByTestId("webform-receipt").textContent).toBeTruthy());
    expect(requests.map((request) => [request.url, request.init?.method])).toEqual([
      ["/forms/product-feedback", "GET"],
      ["/forms/product-feedback", "POST"],
    ]);
  });
});
