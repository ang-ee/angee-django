// @vitest-environment happy-dom

import { act, cleanup, render, renderHook } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { useForm } from "react-hook-form";
import { boundedGraphQLTransportError } from "@angee/refine";
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
  invalidFormSubmit,
  jsonSchemaErrorMessage,
  lineRowErrorsFromDottedPaths,
  markMemberErrors,
  MEMBER_ERRORS,
  messagesForDottedPath,
  serverErrorsFromForm,
  useDottedPathFieldErrors,
  useRefusalBannerFocus,
  validationErrorMap,
  validationErrorMessages,
  validationErrorsFromError,
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
    // The form-level messages stay several: a list, read by its one reader; the message is their text, one per line.
    const stored = result.current.getFieldState("root.server").error;
    expect(formErrorsFromForm({ root: { server: stored } })).toEqual(["Review the form", "rowsExtra: Unknown field"]);
    expect(formErrorsFromForm({ root: { server: { type: "server", message: "Set by another owner." } } })).toEqual(["Set by another owner."]);
    expect(formErrorsFromForm({})).toEqual([]);
    expect(stored).toMatchObject({
      type: "server", message: "Review the form\nrowsExtra: Unknown field", types: { server: ["Review the form", "rowsExtra: Unknown field"] },
    });
  });

  test("a refusal bound to a rendered field is shown there once, not again as the fallback", () => {
    const { result } = renderHook(() => useForm<Record<string, unknown>>());
    act(() => {
      applyFormErrors(result.current, invalidFormSubmit({ fieldErrors: { url: ["The address is not allowed."] }, formErrors: [] }),
        { fieldNames: ["url"], fallback: "Could not subscribe." });
    });
    expect(result.current.getFieldState("url").error?.message).toBe("The address is not allowed.");
    expect(result.current.getFieldState("root.server").error).toBeUndefined();
    act(() => {
      result.current.clearErrors();
      applyFormErrors(result.current, invalidFormSubmit({ fieldErrors: {}, formErrors: [] }),
        { fieldNames: ["url"], fallback: "Could not subscribe." });
    });
    expect(result.current.getFieldState("root.server").error?.message).toBe("Could not subscribe.");
  });
});

test("normalized action outcomes retain their transport-owned validation map", () => {
  const outcome = { ok: false, message: "Review", validationErrors: { title: ["Required"] } };
  expect(actionOutcomeSubmitResult(outcome)).toEqual(invalidFormSubmit({ fieldErrors: outcome.validationErrors, formErrors: ["Review"] }));
});

test.each(["STALE_REVISION", "CREATION_KEY_CONFLICT"])("decodes %s as the shared form conflict", (code) => {
  const error = { graphQLErrors: [{ message: "Record changed", extensions: { code } }] };
  expect(formSubmitError(error)).toEqual({ status: "conflict", message: "Record changed" });
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

describe("where a refusal brings the person (applyFormErrors with `focus`)", () => {
  type Issues = { fieldErrors: Record<string, string[]>; formErrors: string[] };
  /** Apply a refusal to a form that shows `label` and `code`, in that order, and report what was asked. */
  function refuse(issues: Issues, ask = true) {
    const setError = vi.fn();
    const focus = vi.fn();
    applyFormErrors({ setError } as never, { status: "invalid", issues }, {
      fieldNames: ["label", "code"],
      fieldSummary: (errors) => `Fix: ${Object.keys(errors).join(", ")}`,
      fallback: "Save failed.",
      ...(ask ? { focus } : {}),
    });
    const banner = setError.mock.calls.find(([name]) => name === "root.server")?.[1] as { types: { server: string[] } } | undefined;
    return {
      fieldFocused: setError.mock.calls.filter(([, , options]) => (options as { shouldFocus?: boolean } | undefined)?.shouldFocus).map(([name]) => name),
      bannerAsked: focus.mock.calls.length,
      banner: banner?.types.server ?? [],
    };
  }

  test("field reasons only: the first refused field in the form's order takes focus, and the banner is not asked", () => {
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

describe("useRefusalBannerFocus", () => {
  afterEach(() => { cleanup(); document.body.replaceChildren(); });
  /** A banner element and a refused field in the document, and the hook over a list of messages. */
  function mount() {
    const banner = document.body.appendChild(document.createElement("div"));
    banner.tabIndex = -1;
    const field = document.body.appendChild(document.createElement("input"));
    field.setAttribute("aria-invalid", "true");
    const other = document.body.appendChild(document.createElement("button"));
    const hook = renderHook(({ messages }: { messages: readonly string[] }) => {
      const focus = useRefusalBannerFocus(messages);
      focus.banner.ref.current = banner;
      return focus;
    }, { initialProps: { messages: [] as readonly string[] } });
    return { banner, field, other, hook };
  }

  test("asked by the refusal's owner, the banner takes focus when the new list lands, also from a refused field", () => {
    const { banner, field, hook } = mount();
    field.focus();
    hook.result.current.focusBanner();
    expect(document.activeElement).toBe(field);
    hook.rerender({ messages: ["The note is misconfigured."] });
    expect(document.activeElement).toBe(banner);
  });

  test("not asked, the banner leaves focus in a refused field, and takes it from anywhere else", () => {
    const { banner, field, other, hook } = mount();
    field.focus();
    hook.rerender({ messages: ["Fix: code"] });
    expect(document.activeElement).toBe(field);
    other.focus();
    hook.rerender({ messages: ["Fix: code, label"] });
    expect(document.activeElement).toBe(banner);
  });

  test("asking is spent by one list: a later list that is not asked for leaves a refused field its focus", () => {
    const { banner, field, hook } = mount();
    hook.result.current.focusBanner();
    hook.rerender({ messages: ["The note is misconfigured."] });
    expect(document.activeElement).toBe(banner);
    field.focus();
    hook.rerender({ messages: ["Fix: code"] });
    expect(document.activeElement).toBe(field);
  });

  test("no list, no focus", () => {
    const { other, hook } = mount();
    other.focus();
    hook.result.current.focusBanner();
    hook.rerender({ messages: [] });
    expect(document.activeElement).toBe(other);
  });
});
