// @vitest-environment happy-dom

import { act, cleanup, render, renderHook, waitFor } from "@testing-library/react";
import { Activity, createElement } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { useForm } from "react-hook-form";
import { boundedGraphQLTransportError, type ActionOutcome } from "@angee/refine";
import { errorFromUnknown } from "../../data/errors";
import { enUiBundle } from "../../i18n";
import { createAngeeI18nInstance } from "../../runtime/i18n";
import { fieldErrorMessages } from "./form-view-model";

import {
  actionFormSubmitResult,
  actionOutcomeSubmitResult,
  applyFormErrors,
  directDottedPathMessages,
  formErrorsFromForm,
  formSubmitError,
  formSubmitOutcome,
  invalidFormSubmit,
  jsonSchemaErrorMessage,
  markMemberErrors,
  MEMBER_ERRORS,
  messagesForDottedPath,
  serverErrorsFromForm,
  useDottedPathFieldErrors,
  useRefusalFocus,
  validationErrorMap,
  validationErrorMessages,
  validationErrorsFromError,
  type FormSubmitResult,
  type ValidationErrors,
} from "./validation-errors";

const t = createAngeeI18nInstance(enUiBundle).getFixedT(null, "ui");

test.each([
  { keyword: "minLength", limit: 1, message: "Enter at least 1 character." },
  { keyword: "minLength", limit: 3, message: "Enter at least 3 characters." },
  { keyword: "maxLength", limit: 1, message: "Enter no more than 1 character." },
  { keyword: "maxLength", limit: 64, message: "Enter no more than 64 characters." },
  { keyword: "minItems", limit: 1, message: "Add at least 1 item." },
  { keyword: "minItems", limit: 2, message: "Add at least 2 items." },
  { keyword: "maxItems", limit: 1, message: "Keep no more than 1 item." },
  { keyword: "maxItems", limit: 3, message: "Keep no more than 3 items." },
  { keyword: "minimum", limit: 1, message: "Enter a value of at least 1." },
  { keyword: "maximum", limit: 1, message: "Enter a value of at most 1." },
  { keyword: "exclusiveMinimum", limit: 1, message: "Enter a value greater than 1." },
  { keyword: "exclusiveMaximum", limit: 1, message: "Enter a value less than 1." },
])("a failed $keyword with a limit of $limit says: $message", ({ keyword, limit, message }) => {
  expect(jsonSchemaErrorMessage({ keyword, params: { limit } }, t)).toBe(message);
});

describe("markMemberErrors", () => {
  const leaf = (type: string) => ({ type, message: `Fails ${type}.` });
  // An object field owns a template of its members; any other field does not.
  const objectField = (name: string) => ({ name, objectTemplate: [{ name: "handle" }, { name: "delay" }] });

  test("an object field's node of member errors gets the members' type and no message; the tree is the one given", () => {
    const errors = { retry: { handle: leaf("minLength"), delay: leaf("maximum") } };
    expect(markMemberErrors(errors, [objectField("retry")])).toBe(errors);
    expect(errors).toStrictEqual({ retry: { type: MEMBER_ERRORS, handle: leaf("minLength"), delay: leaf("maximum") } });
  });

  test("an object field inside a dotted root is marked at its own path; the root above it and the object below it are not", () => {
    const errors = { config: { retry: { handle: leaf("minLength"), backoff: { factor: leaf("minimum") } } } };
    markMemberErrors(errors, [objectField("config.retry")]);
    expect(errors).toStrictEqual({
      config: { retry: { type: MEMBER_ERRORS, handle: leaf("minLength"), backoff: { factor: leaf("minimum") } } },
    });
  });

  test.each([
    {
      node: "the node of a field without a template, though a bare object of errors sits there",
      errors: () => ({ config: { timeout: leaf("type") } }), fields: [{ name: "config" }, { name: "config.timeout" }],
    },
    { node: "a scalar field's leaf error", errors: () => ({ title: leaf("required") }), fields: [{ name: "title" }] },
    { node: "an object field's own leaf error", errors: () => ({ retry: leaf("required") }), fields: [objectField("retry")] },
    {
      node: "an object field's leaf error with a message and no type",
      errors: () => ({ retry: { message: "Enter a retry." } }), fields: [objectField("retry")],
    },
    {
      node: "an object field's node that has a type of its own",
      errors: () => ({ retry: { ...leaf("server"), handle: leaf("required") } }), fields: [objectField("retry")],
    },
    {
      node: "an object field's array node (a member named with a number)",
      errors: () => ({ retry: [leaf("required")] }), fields: [{ name: "retry", objectTemplate: [{ name: "0" }] }],
    },
    {
      node: "an unregistered root of member errors",
      errors: () => ({ config: { token: leaf("required") } }), fields: [{ name: "config.token" }],
    },
    { node: "an object field with no error", errors: () => ({ title: leaf("required") }), fields: [objectField("retry")] },
  ])("$node is left as it is", ({ errors, fields }) => {
    const given = errors();
    expect(markMemberErrors(given, fields)).toBe(given);
    expect(given).toStrictEqual(errors());
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

describe("React Hook Form's own `type` and `ref` beside a member so named", () => {
  const rule = { type: "minLength", message: "Enter at least 3 characters." };
  const deeper = { kind: { type: "required", message: "Choose a kind." } };
  const noop = () => undefined;

  test.each([
    { row: "`type` a string", error: { ...rule }, messages: ["Enter at least 3 characters."] },
    { row: "`type` a number", error: { type: 3, message: "Enter a code." }, messages: ["Enter a code."] },
    {
      row: "`type` the marker MEMBER_ERRORS", error: { type: MEMBER_ERRORS, handle: rule },
      messages: ["retry.handle: Enter at least 3 characters."],
    },
    { row: "`type` an error node", error: { type: rule }, messages: ["retry.type: Enter at least 3 characters."] },
    { row: "`type` deeper member errors", error: { type: deeper }, messages: ["retry.type.kind: Choose a kind."] },
    {
      // The field refused, then its member: React Hook Form's setError spreads the field's
      // `type: "server"` into the member's error, a letter at each index key.
      row: "`type` an error node with letter keys",
      error: {
        type: {
          0: "s", 1: "e", 2: "r", 3: "v", 4: "e", 5: "r",
          type: "server", message: "Bad kind.", types: { server: ["Bad kind."] },
        },
        message: "Bad retry.", types: { server: ["Bad retry."] },
      },
      messages: ["Bad retry.", "retry.type: Bad kind."],
    },
    { row: "`type` null", error: { type: null, message: "Enter a kind." }, messages: ["Enter a kind."] },
    { row: "`type` true", error: { type: true, message: "Enter a kind." }, messages: ["Enter a kind."] },
    { row: "a bare string given", error: "Enter a kind.", messages: ["Enter a kind."] },
    {
      row: "`types` beside a message",
      error: { type: "required", message: "Enter a kind.", types: { required: "Enter a kind.", minLength: "Too short." } },
      messages: ["Enter a kind."],
    },
    {
      row: "`types` with no message",
      error: { type: "required", types: { required: "Enter a kind.", minLength: ["Too short.", "Far too short."] } },
      messages: ["Enter a kind.", "Too short.", "Far too short."],
    },
    {
      row: "`types` holding a rule named `type`, whose text stays skipped",
      error: { types: { type: "Enter a valid value.", required: "Enter a kind." } },
      messages: ["Enter a kind."],
    },
    {
      row: "`ref` a Controller proxy",
      error: { type: "server", message: "Taken.", ref: { focus: noop, select: noop, setCustomValidity: noop, reportValidity: noop } },
      messages: ["Taken."],
    },
    {
      row: "`ref` { name }, no element attached",
      error: { type: "server", message: "Taken.", ref: { name: "retry" } }, messages: ["Taken."],
    },
    {
      row: "`ref` { type, name } of radios",
      error: { type: "required", message: "Choose one.", ref: { type: "radio", name: "retry" } }, messages: ["Choose one."],
    },
  ])("$row: the field's messages are exactly the failing members' and its own", ({ error, messages }) => {
    expect(fieldErrorMessages([error], "retry")).toEqual(messages);
  });

  test("`ref` a DOM element React rendered, which leads to the renderer's own objects: the field's message is exactly its own", () => {
    const { container } = render(createElement("input"));
    try {
      const element = container.querySelector("input");
      expect(element).toBeInstanceOf(HTMLInputElement);
      expect(fieldErrorMessages([{ type: "server", message: "Taken.", ref: element }], "retry")).toEqual(["Taken."]);
    } finally {
      cleanup();
    }
  });

  test.fails.each([
    { row: "`ref` an error node", error: { ref: rule }, messages: ["retry.ref: Enter at least 3 characters."] },
    { row: "`ref` deeper member errors", error: { ref: deeper }, messages: ["retry.ref.kind: Choose a kind."] },
  ])("defect: $row gives no message, since `ref` is never walked; it should give the member's message", ({ error, messages }) => {
    expect(fieldErrorMessages([error], "retry")).toEqual(messages);
  });

  // The form's errors as a render reads them, which subscribes the hook to them.
  function refusedMember(member: string) {
    const { result } = renderHook(() => {
      const form = useForm<Record<string, unknown>>();
      return { form, errors: form.formState.errors };
    });
    act(() => { applyFormErrors(result.current.form, invalidFormSubmit({ fieldErrors: { [`retry.${member}`]: ["Taken."] }, formErrors: [] })); });
    return serverErrorsFromForm(result.current.errors);
  }

  test("serverErrorsFromForm keeps a server refusal of a member named `type` at its dotted path", () => {
    expect(refusedMember("type")).toEqual({ "retry.type": ["Taken."] });
  });

  test.fails("defect: serverErrorsFromForm drops a server refusal of a member named `ref`, since it never walks `ref`; it should keep it at its dotted path", () => {
    expect(refusedMember("ref")).toEqual({ "retry.ref": ["Taken."] });
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
    const shownFieldNames = ["review", "rows"];
    const { result } = renderHook(() =>
      useDottedPathFieldErrors(shownFieldNames),
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
    // The wire's message joins the refusal's reasons: the field's reason stays under its key, said once.
    expect(actionFormSubmitResult({ saved: { ok: false, message: "Required", id: null, validation_errors: { title: ["Required"] } } }, "saved")).toEqual({
      status: "invalid", issues: { fieldErrors: { title: ["Required"] }, formErrors: [] },
    });
    expect(actionFormSubmitResult(null, "saved")).toEqual({ status: "invalid", issues: { fieldErrors: {}, formErrors: [] } });
  });

  test("binds dotted issue paths and keeps their messages without duplicating descendants in the summary", () => {
    const { result } = renderHook(() => useForm<Record<string, unknown>>());
    act(() => {
      applyFormErrors(result.current, invalidFormSubmit({
        fieldErrors: { "rows.0.title": ["Required", "Use a unique title"], rowsExtra: ["Unknown field"] },
        formErrors: ["Review the form"],
      }), { shownFieldNames: ["rows"] });
    });
    expect(result.current.getFieldState("rows.0.title").error).toMatchObject({
      type: "server", message: "Required Use a unique title", types: { server: ["Required", "Use a unique title"] },
    });
    // The form-level messages stay several: a list, read by its one reader; the message is their text, one per line.
    const stored = result.current.getFieldState("root.server").error;
    expect(formErrorsFromForm({ root: { server: stored } })).toEqual(["Review the form", "rowsExtra: Unknown field"]);
    expect(formErrorsFromForm({ root: { server: { type: "server", message: "Set by another owner." } } })).toEqual(["Set by another owner."]);
    expect(formErrorsFromForm({})).toEqual([]);
    expect(stored).toMatchObject({
      type: "server", message: "Review the form\nrowsExtra: Unknown field", types: { server: ["Review the form", "rowsExtra: Unknown field"] },
    });
  });

  test("one form-level error reads as one list, so the list is new exactly when a new answer lands", () => {
    const { result } = renderHook(() => useForm<Record<string, unknown>>());
    act(() => result.current.setError("root.server", { type: "server", message: "Set by another owner." }));
    const first = result.current.getFieldState("root.server").error!;
    // A message alone, as another owner sets it: two reads of the one error object give the same list.
    expect(formErrorsFromForm({ root: { server: first } })).toBe(formErrorsFromForm({ root: { server: first } }));
    expect(formErrorsFromForm({ root: { server: first } })).toEqual(["Set by another owner."]);
    act(() => result.current.setError("root.server", { type: "server", message: "Set by another owner." }));
    const second = result.current.getFieldState("root.server").error!;
    expect(second).not.toBe(first);
    expect(formErrorsFromForm({ root: { server: second } })).not.toBe(formErrorsFromForm({ root: { server: first } }));
  });

  test("a refusal bound to a rendered field is shown there once, not again as the fallback", () => {
    const { result } = renderHook(() => useForm<Record<string, unknown>>());
    act(() => {
      applyFormErrors(result.current, invalidFormSubmit({ fieldErrors: { url: ["The address is not allowed."] }, formErrors: [] }),
        { shownFieldNames: ["url"], fallback: "Could not subscribe." });
    });
    expect(result.current.getFieldState("url").error?.message).toBe("The address is not allowed.");
    expect(result.current.getFieldState("root.server").error).toBeUndefined();
    act(() => {
      result.current.clearErrors();
      applyFormErrors(result.current, invalidFormSubmit({ fieldErrors: {}, formErrors: [] }),
        { shownFieldNames: ["url"], fallback: "Could not subscribe." });
    });
    expect(result.current.getFieldState("root.server").error?.message).toBe("Could not subscribe.");
  });

  test("an empty list of shown fields claims none: every field reason shows on the banner, as `path: reason`, and the banner is asked", () => {
    const { result } = renderHook(() => useForm<Record<string, unknown>>());
    const focus = vi.fn();
    act(() => {
      applyFormErrors(result.current, invalidFormSubmit({
        fieldErrors: { url: ["The address is not allowed."], "rows.0.qty": ["Too many."] }, formErrors: [],
      }), { shownFieldNames: [], focus });
    });
    // Each reason is still set at its path, for a control a page renders itself.
    expect(result.current.getFieldState("url").error?.message).toBe("The address is not allowed.");
    expect(formErrorsFromForm({ root: { server: result.current.getFieldState("root.server").error } }))
      .toEqual(["url: The address is not allowed.", "rows.0.qty: Too many."]);
    expect(focus.mock.calls).toEqual([[undefined]]);
  });
});

// Each shape of refusal the server sends (`ActionResult.from_error`): its `message` joins every
// validation message, a refused target's is the verb's summary, and non-field reasons sit under `__all__`.
test.each<[string, ActionOutcome, ValidationErrors]>([
  ["one field: its reason under its key, and no form-level reason",
    { ok: false, message: "Too early.", validationErrors: { date: ["Too early."] } },
    { fieldErrors: { date: ["Too early."] }, formErrors: [] }],
  ["two fields: both under their keys, and no form-level reason",
    { ok: false, message: "Write a subject.; Too early.", validationErrors: { subject: ["Write a subject."], date: ["Too early."] } },
    { fieldErrors: { subject: ["Write a subject."], date: ["Too early."] }, formErrors: [] }],
  ["fields and a non-field reason: only that reason at form level, without its key",
    { ok: false, message: "Too early.; The period is closed.", validationErrors: { date: ["Too early."], __all__: ["The period is closed."] } },
    { fieldErrors: { date: ["Too early."] }, formErrors: ["The period is closed."] }],
  ["non-field reasons only, with a map: each reason once, at form level",
    { ok: false, message: "The period is closed.; The ledger is locked.", validationErrors: { __all__: ["The period is closed.", "The ledger is locked."] } },
    { fieldErrors: {}, formErrors: ["The period is closed.", "The ledger is locked."] }],
  ["a plain message with no map: the message stands in",
    { ok: false, message: "The period is closed." },
    { fieldErrors: {}, formErrors: ["The period is closed."] }],
  ["a map whose only key holds no reason (`__all__`): the message stands in",
    { ok: false, message: "Send failed.", validationErrors: { __all__: [] } },
    { fieldErrors: {}, formErrors: ["Send failed."] }],
  ["a map whose only key holds no reason (a field's): the message stands in, and the empty key binds nothing",
    { ok: false, message: "Send failed.", validationErrors: { to: [] } },
    { fieldErrors: { to: [] }, formErrors: ["Send failed."] }],
  ["a domain error in band: the message stands in",
    { ok: false, message: "RECORD_ACCESS_SUBJECT_REFUSED", code: "RECORD_ACCESS_SUBJECT_REFUSED" },
    { fieldErrors: {}, formErrors: ["RECORD_ACCESS_SUBJECT_REFUSED"] }],
  ["a refused target: its reason, not the verb's summary",
    { ok: false, message: "Confirm failed.", validationErrors: { __all__: ["You are not allowed to modify this sales order."] } },
    { fieldErrors: {}, formErrors: ["You are not allowed to modify this sales order."] }],
  ["a reason under a key no field has: left in the map, for the binder to show",
    { ok: false, message: "A saved project is required.", validationErrors: { target: ["A saved project is required."] } },
    { fieldErrors: { target: ["A saved project is required."] }, formErrors: [] }],
])("an action's refusal says each reason once: %s", (_shape, outcome, issues) => {
  expect(actionOutcomeSubmitResult(outcome)).toEqual(invalidFormSubmit(issues));
});

test.each(["STALE_REVISION", "CREATION_KEY_CONFLICT"] as const)("decodes %s as the shared form conflict, carrying its code", (code) => {
  const error = { graphQLErrors: [{ message: "Record changed", extensions: { code } }] };
  expect(formSubmitError(error)).toEqual({ status: "conflict", code, message: "Record changed" });
});

test("an action outcome refused with a conflict code is a conflict carrying it; any other code stays a refusal", () => {
  expect(actionOutcomeSubmitResult({ ok: false, code: "CREATION_KEY_CONFLICT", message: "Created earlier." }))
    .toEqual({ status: "conflict", code: "CREATION_KEY_CONFLICT", message: "Created earlier." });
  expect(actionOutcomeSubmitResult({ ok: false, code: "VALIDATION", message: "Review" }))
    .toEqual(invalidFormSubmit({ fieldErrors: {}, formErrors: ["Review"] }));
});

test.each<[string, FormSubmitResult<unknown>, ReturnType<typeof formSubmitOutcome>]>([
  ["an accepted result confirms the key", { status: "ok", data: null }, null],
  ["a creation-key conflict says the key is spent", { status: "conflict", code: "CREATION_KEY_CONFLICT", message: "x" }, "conflict"],
  ["a stale revision refuses it", { status: "conflict", code: "STALE_REVISION", message: "x" }, "refused"],
  ["a bare conflict is a stale one and refuses it", { status: "conflict", message: "x" }, "refused"],
  ["a validation refusal refuses it", { status: "invalid", issues: { fieldErrors: {}, formErrors: ["x"] } }, "refused"],
  ["issues marked unconfirmed keep it as possibly written", { status: "invalid", issues: { fieldErrors: {}, formErrors: ["x"] }, outcome: "unconfirmed" }, "unconfirmed"],
])("what a submit's result tells its key: %s", (_name, result, outcome) => {
  expect(formSubmitOutcome(result)).toBe(outcome);
});

test.each([
  ["with the surface's sentence", "It may have been sent.", ["It may have been sent."]],
  ["without one, reading what the transport tells", undefined, ["Request failed."]],
])("a lost answer converted to a result is marked unconfirmed, %s", (_name, answerLost, formErrors) => {
  const result = formSubmitError(boundedGraphQLTransportError(new TypeError("Failed to fetch")), "Could not create.", answerLost);
  expect(result).toMatchObject({ status: "invalid", outcome: "unconfirmed", issues: { fieldErrors: {} } });
  expect(result.status === "invalid" && result.issues.formErrors.slice(0, 1)).toEqual(formErrors);
  expect(formSubmitOutcome(result)).toBe("unconfirmed");
});

test("a caller's own refusal worded from its message is no lost answer", () => {
  const result = formSubmitError("Could not create.", undefined, "It may have been sent.");
  expect(result).toEqual(invalidFormSubmit({ fieldErrors: {}, formErrors: ["Could not create."] }));
  expect(formSubmitOutcome(result)).toBe("refused");
});

test("a creation-key conflict does not lock a draft as a stale edit does: its form error is its own type", () => {
  const { result } = renderHook(() => useForm<Record<string, unknown>>());
  act(() => { applyFormErrors(result.current, { status: "conflict", code: "CREATION_KEY_CONFLICT", message: "Created earlier." }); });
  expect(result.current.getFieldState("root.server").error?.type).toBe("creationKeyConflict");
  act(() => { applyFormErrors(result.current, { status: "conflict", message: "Record changed" }); });
  expect(result.current.getFieldState("root.server").error?.type).toBe("conflict");
});

test.each([undefined, { id: "old-result" }, { status: "later" }, { status: "ok" }, { status: "invalid", issues: {} },
  { status: "invalid", issues: { fieldErrors: {}, formErrors: [] }, outcome: "refused" }])("malformed submit results fail loudly instead of becoming a retryable form error: %j", (malformed) => {
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

describe("where a refusal brings the person (applyFormErrors with `focus`)", () => {
  type Issues = { fieldErrors: Record<string, string[]>; formErrors: string[] };
  /** Apply a refusal to a form that shows `label` and `code`, in that order, and report what was asked. */
  function refuse(issues: Issues, ask = true) {
    const setError = vi.fn();
    const focus = vi.fn();
    applyFormErrors({ setError } as never, { status: "invalid", issues }, {
      shownFieldNames: ["label", "code"],
      fieldSummary: (errors) => `Fix: ${Object.keys(errors).join(", ")}`,
      fallback: "Save failed.",
      ...(ask ? { focus } : {}),
    });
    // The binder never focuses itself (the form library's `shouldFocus`): the surface's hook does, when it can.
    expect(setError.mock.calls.every(([, , options]) => options === undefined)).toBe(true);
    const banner = setError.mock.calls.find(([name]) => name === "root.server")?.[1] as { types: { server: string[] } } | undefined;
    return {
      fieldFocused: focus.mock.calls.flatMap(([field]) => field === undefined ? [] : [field]),
      bannerAsked: focus.mock.calls.filter(([field]) => field === undefined).length,
      banner: banner?.types.server ?? [],
    };
  }

  test("field reasons only: the surface is asked to focus the first refused field in the form's order, not the banner", () => {
    expect(refuse({ fieldErrors: { code: ["Taken."], label: ["Too long."] }, formErrors: [] }))
      .toEqual({ fieldFocused: ["label"], bannerAsked: 0, banner: ["Fix: code, label"] });
  });

  test("a form-level reason beside a field reason: the banner is asked, carries the line naming the field, and no field takes focus", () => {
    expect(refuse({ fieldErrors: { code: ["Taken."] }, formErrors: ["The note is misconfigured."] }))
      .toEqual({ fieldFocused: [], bannerAsked: 1, banner: ["The note is misconfigured.", "Fix: code"] });
  });

  test("a reason of a field the form does not show counts as one no field shows", () => {
    expect(refuse({ fieldErrors: { code: ["Taken."], secret: ["Refused."] }, formErrors: [] }))
      .toEqual({ fieldFocused: [], bannerAsked: 1, banner: ["Fix: code", "secret: Refused."] });
  });

  test("a failure that names nothing: the banner is asked and holds the fallback", () => {
    expect(refuse({ fieldErrors: {}, formErrors: [] })).toEqual({ fieldFocused: [], bannerAsked: 1, banner: ["Save failed."] });
  });

  test("an owner that does not ask moves no focus, whatever the refusal holds", () => {
    expect(refuse({ fieldErrors: { code: ["Taken."] }, formErrors: [] }, false)).toMatchObject({ fieldFocused: [], bannerAsked: 0 });
    expect(refuse({ fieldErrors: { code: ["Taken."] }, formErrors: ["The note is misconfigured."] }, false)).toMatchObject({ fieldFocused: [], bannerAsked: 0 });
  });
});

describe("useRefusalFocus", () => {
  afterEach(() => { cleanup(); document.body.replaceChildren(); });
  /**
   * A banner element and a refused field `label` in the document, and the hook over a list of
   * messages. Its form's `setFocus` focuses `label`'s control on a timer, as the form library's
   * own does; `none` names a field with no control to focus.
   */
  function mount() {
    const banner = document.body.appendChild(document.createElement("div"));
    banner.tabIndex = -1;
    const field = document.body.appendChild(document.createElement("input"));
    field.setAttribute("aria-invalid", "true");
    const other = document.body.appendChild(document.createElement("button"));
    const setFocus = vi.fn((name: string) => { setTimeout(() => { if (name === "label") field.focus(); }); });
    const bannerFocused = vi.fn();
    banner.addEventListener("focus", bannerFocused);
    const hook = renderHook(({ messages, ready }: { messages: readonly string[]; ready: boolean }) => {
      const focus = useRefusalFocus(messages, { form: { setFocus } as never, ready });
      focus.banner.ref.current = banner;
      return focus;
    }, { initialProps: { messages: [] as readonly string[], ready: true } });
    return { banner, field, other, hook, setFocus, bannerFocused };
  }

  test("asked by the refusal's owner, the banner takes focus when the new list lands, also from a refused field", () => {
    const { banner, field, hook } = mount();
    field.focus();
    act(() => hook.result.current.focus());
    expect(document.activeElement).toBe(field);
    hook.rerender({ messages: ["The note is misconfigured."], ready: true });
    expect(document.activeElement).toBe(banner);
  });

  test("asked for a field, it focuses that field through the form's own setFocus, and the banner keeps out", async () => {
    const { field, other, hook, setFocus, bannerFocused } = mount();
    other.focus();
    act(() => hook.result.current.focus("label"));
    hook.rerender({ messages: ["Fix: label"], ready: true });
    await waitFor(() => expect(document.activeElement).toBe(field));
    expect(setFocus.mock.calls).toEqual([["label"]]);
    expect(bannerFocused).not.toHaveBeenCalled();
  });

  // `ready` guards no path a shipped surface takes today: each commits the refusal and the end of
  // its submit in one batch. It is what keeps the hook from depending on that batching, and this
  // test is its one proof.
  test("not ready (its controls locked), the request waits, and is carried out once the surface is ready", async () => {
    const { field, other, hook, setFocus } = mount();
    hook.rerender({ messages: [], ready: false });
    other.focus();
    act(() => hook.result.current.focus("label"));
    hook.rerender({ messages: ["Fix: label"], ready: false });
    expect(setFocus).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(other);
    hook.rerender({ messages: ["Fix: label"], ready: true });
    await waitFor(() => expect(document.activeElement).toBe(field));
    expect(setFocus.mock.calls).toEqual([["label"]]);
  });

  test("a field whose control takes no focus leaves it to the banner", async () => {
    const { banner, other, hook } = mount();
    other.focus();
    act(() => hook.result.current.focus("none"));
    hook.rerender({ messages: ["Fix: none"], ready: true });
    await waitFor(() => expect(document.activeElement).toBe(banner));
  });

  test("not asked, the banner leaves focus in a refused field, and takes it from anywhere else", () => {
    const { banner, field, other, hook } = mount();
    field.focus();
    hook.rerender({ messages: ["Fix: code"], ready: true });
    expect(document.activeElement).toBe(field);
    other.focus();
    hook.rerender({ messages: ["Fix: code, label"], ready: true });
    expect(document.activeElement).toBe(banner);
  });

  test("asking is spent by one list: a later list that is not asked for leaves a refused field its focus", () => {
    const { banner, field, hook } = mount();
    act(() => hook.result.current.focus());
    hook.rerender({ messages: ["The note is misconfigured."], ready: true });
    expect(document.activeElement).toBe(banner);
    field.focus();
    hook.rerender({ messages: ["Fix: code"], ready: true });
    expect(document.activeElement).toBe(field);
  });

  test("no list, no focus", () => {
    const { other, hook } = mount();
    other.focus();
    act(() => hook.result.current.focus());
    hook.rerender({ messages: [], ready: true });
    expect(document.activeElement).toBe(other);
  });

  test("not asked, the same list still showing moves no focus when the surface is ready again", () => {
    const { banner, other, hook, bannerFocused } = mount();
    const list = ["The period is closed."];
    hook.rerender({ messages: list, ready: true });
    expect(document.activeElement).toBe(banner);
    other.focus();
    bannerFocused.mockClear();
    // A submit that never answers (its check threw) locks and frees the controls with the old list showing.
    hook.rerender({ messages: list, ready: false });
    hook.rerender({ messages: list, ready: true });
    expect(document.activeElement).toBe(other);
    expect(bannerFocused).not.toHaveBeenCalled();
  });

  test.each([
    ["in the render of its request", false],
    ["one render after its request, while the field's focus is on its way", true],
  ] as const)("a field's own list, landing %s, is handled by the field: ready again, the banner keeps out", async (_when, apart) => {
    const { field, other, hook, bannerFocused } = mount();
    const list = ["Fix: label"];
    if (apart) {
      act(() => hook.result.current.focus("label"));
      hook.rerender({ messages: list, ready: true });
    } else {
      act(() => { hook.result.current.focus("label"); hook.rerender({ messages: list, ready: true }); });
    }
    await waitFor(() => expect(document.activeElement).toBe(field));
    // The banner's turn is the timer after the field's: let it pass before the surface locks again.
    await act(() => new Promise<void>((resolve) => { setTimeout(resolve); }));
    other.focus();
    hook.rerender({ messages: list, ready: false });
    hook.rerender({ messages: list, ready: true });
    expect(document.activeElement).toBe(other);
    expect(bannerFocused).not.toHaveBeenCalled();
  });

  test("hidden while a field's focus is on its way, then shown again, it still carries out the next request", () => {
    const setFocus = vi.fn();
    const hook: { current?: ReturnType<typeof useRefusalFocus> } = {};
    function Surface() {
      hook.current = useRefusalFocus([], { form: { setFocus } as never });
      return null;
    }
    const view = (mode: "visible" | "hidden") => createElement(Activity, { mode, children: createElement(Surface) });
    const { rerender } = render(view("visible"));
    act(() => hook.current!.focus("label"));
    expect(setFocus).toHaveBeenCalledTimes(1);
    // Hidden, the surface's effects are cleaned up with that field's focus still on its way.
    act(() => rerender(view("hidden")));
    act(() => rerender(view("visible")));
    act(() => hook.current!.focus("label"));
    expect(setFocus).toHaveBeenCalledTimes(2);
  });
});
