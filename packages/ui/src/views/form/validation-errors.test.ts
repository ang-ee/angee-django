// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { boundedGraphQLTransportError } from "@angee/refine";
import { errorFromUnknown } from "../../data/errors";

import {
  directDottedPathMessages,
  lineRowErrorsFromDottedPaths,
  messagesForDottedPath,
  useDottedPathFieldErrors,
  validationErrorMap,
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
