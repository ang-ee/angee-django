import { describe, expect, test } from "vitest";

import { validationErrorsFromError } from "./validation-errors";

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
