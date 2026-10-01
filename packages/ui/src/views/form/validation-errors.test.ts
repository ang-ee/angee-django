// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { useForm } from "react-hook-form";
import { boundedGraphQLTransportError } from "@angee/refine";
import { errorFromUnknown } from "../../data/errors";
import { fieldErrorMessages } from "./form-view-model";

import {
  actionFormSubmitResult,
  actionOutcomeSubmitResult,
  invalidFormSubmit,
  formSubmitError,
  applyFormErrors,
  directDottedPathMessages,
  lineRowErrorsFromDottedPaths,
  messagesForDottedPath,
  useDottedPathFieldErrors,
  validationErrorMap,
  validationErrorMessages,
  validationErrorsFromError,
} from "./validation-errors";

describe("lineRowErrorsFromDottedPaths", () => {
  test("projects indexed child paths and ignores unrelated or malformed paths", () => {
    expect(
      lineRowErrorsFromDottedPaths(
        {
          "lines.0.label": ["Required"],
          "lines.2.quantity": ["Invalid"],
          "lines.summary": ["Ignored"],
          title: ["Unrelated"],
        },
        "lines",
      ),
    ).toEqual([
      { fieldErrors: { label: ["Required"] }, formErrors: [] },
      undefined,
      { fieldErrors: { quantity: ["Invalid"] }, formErrors: [] },
    ]);
  });
});

describe("dotted path message scoping", () => {
  const messages = [
    "rows.0.target: Choose a target",
    "rows.0.config.name: Enter a name",
    "rows.1.target: Choose another target",
  ];

  test("binds exact and descendant messages at dot boundaries", () => {
    expect(messagesForDottedPath(messages, "rows.0.target")).toEqual([
      "Choose a target",
    ]);
    expect(messagesForDottedPath(messages, "rows.0.config")).toEqual([
      "rows.0.config.name: Enter a name",
    ]);
  });

  test("keeps only direct messages for the owning field summary", () => {
    expect(
      directDottedPathMessages(
        ["Rows are invalid", ...messages],
        "rows",
      ),
    ).toEqual(["Rows are invalid"]);
  });

  test("RHF scoped messages keep exact errors bare and route nested descendants", () => {
    const errors = Object.assign(
      [{ lines: [{ label: { message: "Enter a label" } }] }],
      {
        message: "Documents are invalid",
        type: "validate",
        types: { maxItems: "Documents are invalid" },
        ref: { message: "DOM input details are not validation." },
      },
    );
    const scoped = fieldErrorMessages([errors], "documents");

    expect(scoped).toEqual([
      "Documents are invalid",
      "documents.0.lines.0.label: Enter a label",
    ]);
    expect(directDottedPathMessages(scoped, "documents")).toEqual([
      "Documents are invalid",
    ]);
    expect(messagesForDottedPath(scoped, "documents.0.lines.0.label")).toEqual([
      "Enter a label",
    ]);
  });
});

describe("validationErrorMap", () => {
  test("parses a JSON field-to-messages map without changing dotted paths", () => {
    expect(
      validationErrorMap({
        "review.approved": ["Field required"],
        "rows.0.target": ["Input should be an integer"],
      }),
    ).toEqual({
      "review.approved": ["Field required"],
      "rows.0.target": ["Input should be an integer"],
    });
  });

  test("rejects a malformed JSON error map", () => {
    expect(validationErrorMap({ title: "Required" })).toBeNull();
  });
});

describe("validationErrorMessages", () => {
  test("formats every translated field message through the canonical map", () => {
    expect(
      validationErrorMessages({
        "config.mode": ["Choose a supported mode."],
        timeout: ["Must be positive.", "Must be finite."],
      }),
    ).toEqual([
      "config.mode: Choose a supported mode.",
      "timeout: Must be positive.",
      "timeout: Must be finite.",
    ]);
    expect(validationErrorMessages({ timeout: "Must be positive." })).toEqual([]);
  });
});

describe("useDottedPathFieldErrors", () => {
  test("binds and clears exact dotted descendants and summarizes unmatched keys", () => {
    const fieldNames = ["review", "rows"];
    const { result } = renderHook(() =>
      useDottedPathFieldErrors(fieldNames),
    );

    act(() =>
      result.current.replace({
        review: ["Review is invalid"],
        "review.approved": ["Field required"],
        "rows.0.target": ["Choose a target"],
        rowsExtra: ["Must remain unmatched"],
      }),
    );

    expect(result.current.messagesFor("review")).toEqual([
      "Review is invalid",
      "review.approved: Field required",
    ]);
    expect(result.current.messagesFor("rows")).toEqual([
      "rows.0.target: Choose a target",
    ]);
    expect(result.current.formSummary).toBe(
      "rowsExtra: Must remain unmatched",
    );

    act(() => result.current.clearField("review"));
    expect(result.current.messagesFor("review")).toEqual([]);
    expect(result.current.messagesFor("rows")).toEqual([
      "rows.0.target: Choose a target",
    ]);

    act(() => result.current.clear());
    expect(result.current.formSummary).toBeNull();
  });
});

describe("validationErrorsFromError", () => {
  test("reads a bounded native validation error exactly once", () => {
    const error = boundedGraphQLTransportError({
      request: { variables: { secret: "must-not-render" } },
      response: {
        status: 400,
        errors: [{
          message: "Fix this field.",
          extensions: {
            code: "VALIDATION",
            validationErrors: { "config.local_root": ["Required."] },
            formErrors: ["Check the form."],
          },
        }],
      },
    });

    expect(errorFromUnknown(error)?.message).toBe("Fix this field.");
    expect(validationErrorsFromError(error)).toEqual({
      fieldErrors: { "config.local_root": ["Required."] },
      formErrors: ["Check the form."],
    });
  });

  test("does not use transport messages containing request variables", () => {
    const secret = "form-secret-sentinel";
    const error = Object.assign(new Error(`request variables ${secret}`), {
      request: { variables: { secret } },
      response: { status: 500 },
    });
    expect(validationErrorsFromError(error)).toEqual({
      fieldErrors: {},
      formErrors: ["Request failed."],
    });
  });
  test("splits a structured extension into field and form messages", () => {
    const error = {
      message: "[GraphQL] validation failed",
      request: { variables: { password: "must-not-render" } },
      response: {
        errors: [
          {
            message: "validation failed",
            extensions: {
              code: "VALIDATION",
              validationErrors: {
                "config.local_root": ["This field cannot be blank."],
                clientId: ["This field cannot be blank."],
              },
              formErrors: ["Provider is misconfigured."],
            },
          },
        ],
      },
    };

    expect(validationErrorsFromError(error)).toEqual({
      fieldErrors: {
        "config.local_root": ["This field cannot be blank."],
        clientId: ["This field cannot be blank."],
      },
      formErrors: ["Provider is misconfigured."],
    });
  });

  test("merges field messages across multiple graphQL errors", () => {
    const error = {
      graphQLErrors: [
        {
          message: "Validation failed.",
          extensions: { code: "VALIDATION", validationErrors: { slug: ["Required."] } },
        },
        {
          message: "Validation failed.",
          extensions: { code: "VALIDATION", validationErrors: { slug: ["Too short."] } },
        },
      ],
    };

    expect(validationErrorsFromError(error).fieldErrors).toEqual({
      slug: ["Required.", "Too short."],
    });
  });

  test("falls back to a single form message without a structured extension", () => {
    const error = new Error("[GraphQL] Connection refused");
    expect(validationErrorsFromError(error)).toEqual({
      fieldErrors: {},
      formErrors: ["Connection refused"],
    });
  });

  test("returns empty maps for an unrecognised value", () => {
    expect(validationErrorsFromError(undefined)).toEqual({
      fieldErrors: {},
      formErrors: ["Could not save record."],
    });
  });

  test("uses the bounded fallback for opaque objects", () => {
    expect(validationErrorsFromError({})).toEqual({
      fieldErrors: {},
      formErrors: ["Could not save record."],
    });
    expect(validationErrorsFromError({ message: undefined })).toEqual({
      fieldErrors: {},
      formErrors: ["Could not save record."],
    });
  });
});

describe("shared form submission errors", () => {
  test("adapts action outcomes without losing accepted data or issue messages", () => {
    const outcome = { ok: true, message: "Saved", id: "one" };
    expect(actionFormSubmitResult({ saved: outcome }, "saved")).toEqual({ status: "ok", data: outcome, message: "Saved" });
    expect(actionFormSubmitResult({ saved: { ok: false, message: "Review", id: null, validation_errors: { title: ["Required"] } } }, "saved")).toEqual({
      status: "invalid", issues: { fieldErrors: { title: ["Required"] }, formErrors: ["Review"] },
    });
    expect(actionFormSubmitResult(null, "saved")).toEqual({ status: "invalid", issues: { fieldErrors: {}, formErrors: [] } });
  });

  test("binds dotted issue paths and keeps their messages without duplicating descendants in the summary", () => {
    const { result } = renderHook(() => useForm<Record<string, unknown>>());
    act(() => {
      applyFormErrors(result.current, invalidFormSubmit({
        fieldErrors: { "rows.0.title": ["Required", "Use a unique title"], rowsExtra: ["Unknown field"] },
        formErrors: ["Review the form"],
      }), { fieldNames: ["rows"] });
    });
    expect(result.current.getFieldState("rows.0.title").error).toMatchObject({
      type: "server", message: "Required Use a unique title", types: { server: ["Required", "Use a unique title"] },
    });
    expect(result.current.getFieldState("root.server").error?.message).toBe("Review the form rowsExtra: Unknown field");
  });
});

test("normalized action outcomes retain their transport-owned validation map", () => {
  const outcome = { ok: false, message: "Review", validationErrors: { title: ["Required"] } };
  expect(actionOutcomeSubmitResult(outcome)).toEqual(invalidFormSubmit({ fieldErrors: outcome.validationErrors, formErrors: ["Review"] }));
});

test.each([undefined, { id: "old-result" }, { status: "later" }, { status: "ok" }, { status: "invalid", issues: {} }])("malformed submit results fail loudly instead of becoming a retryable form error: %j", (malformed) => {
  const { result } = renderHook(() => useForm<Record<string, unknown>>());
  expect(() => {
    try {
      // @ts-expect-error Deliberately exercise an outdated or malformed caller at runtime.
      applyFormErrors(result.current, malformed);
    } catch (cause) {
      formSubmitError(cause, "Do not hide the developer error");
    }
  }).toThrow(/FormSubmitResult contract/);
  expect(result.current.getFieldState("root.server").error).toBeUndefined();
});
