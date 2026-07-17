// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import {
  directDottedPathMessages,
  messagesForDottedPath,
  useDottedPathFieldErrors,
  validationErrorMap,
  validationErrorsFromError,
} from "./validation-errors";

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
  test("splits a structured extension into field and form messages", () => {
    const error = {
      message: "[GraphQL] validation failed",
      graphQLErrors: [
        {
          message: "validation failed",
          extensions: {
            code: "VALIDATION",
            validationErrors: {
              slug: ["This field cannot be blank."],
              clientId: ["This field cannot be blank."],
            },
            formErrors: ["Provider is misconfigured."],
          },
        },
      ],
    };

    expect(validationErrorsFromError(error)).toEqual({
      fieldErrors: {
        slug: ["This field cannot be blank."],
        clientId: ["This field cannot be blank."],
      },
      formErrors: ["Provider is misconfigured."],
    });
  });

  test("merges field messages across multiple graphQL errors", () => {
    const error = {
      graphQLErrors: [
        { extensions: { validationErrors: { slug: ["Required."] } } },
        { extensions: { validationErrors: { slug: ["Too short."] } } },
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
});
