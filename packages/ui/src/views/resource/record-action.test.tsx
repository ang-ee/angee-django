// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import type { ActionMutate } from "@angee/refine";
import { beforeEach, describe, expect, test, vi } from "vitest";

import type { ActionContext } from "../page";
import {
  recordActionId,
  useActionResultMutation,
  useActionOutcomeMutation,
  useRecordAction,
  useRecordActionMutation,
  useRecordChromeActionMutation,
  useRecordChromeActionOutcome,
} from "./record-action";

const dataMocks = vi.hoisted(() => ({
  mutate: vi.fn<ActionMutate>(async () => ({ ok: true, message: "Synced" })),
  useActionMutation: vi.fn(),
  settle: vi.fn(),
  useActionResultRun: vi.fn(),
  follow: vi.fn(),
  useCreatedRecordLink: vi.fn(),
  authoredMutate: vi.fn(async () => ({ sync_calendar: { ok: true, message: "Synced" } })),
  useAuthoredResourceMutation: vi.fn(),
  chrome: {
    resource: "messaging.Channel",
    canonicalResource: "integrate.Integration",
    dataProviderName: "console",
    recordId: "chn_1",
    record: { lifecycle: "CONNECTED" },
  },
}));

vi.mock("./record-chrome-context", () => ({
  useRecordChromeContext: () => dataMocks.chrome,
}));

// Keep the real `runActionResult` — the hook under test projects the in-band
// outcome through it — and stub only the mutation owner.
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useActionMutation: dataMocks.useActionMutation,
}));

vi.mock("./action-result-run", () => ({
  useActionResultRun: dataMocks.useActionResultRun,
  useCreatedRecordLink: dataMocks.useCreatedRecordLink,
}));

vi.mock("./authored-resource-mutation", () => ({
  useAuthoredResourceMutation: dataMocks.useAuthoredResourceMutation,
}));

// The metadata edge, faked at the one seam these hooks compose: `@angee/metadata`
// owns folding model labels into refine invalidation params, and `invalidation.ts`
// there owns testing that fold. What the tests below own is which labels reach it,
// and in what order — so the fake keeps the label visible in its output.
vi.mock("@angee/metadata", () => ({
  useCanonicalResourceModelLabels: (modelLabels: readonly string[] | undefined) =>
    (modelLabels ?? []).map((modelLabel) =>
      modelLabel === "Skill" ? "agents.Skill" : modelLabel
    ),
  useResourceInvalidates: (modelLabels: readonly string[] | undefined) =>
    (modelLabels ?? []).map((modelLabel) => ({
      dataProviderName: "console",
      invalidates: ["list"],
      resource: modelLabel,
    })),
}));

describe("record action helpers", () => {
  beforeEach(() => {
    dataMocks.mutate.mockClear();
    dataMocks.settle.mockReset();
    dataMocks.settle.mockImplementation(async (fire: () => Promise<unknown>) => {
      return fire();
    });
    dataMocks.useActionResultRun.mockReset();
    dataMocks.useActionResultRun.mockReturnValue(dataMocks.settle);
    dataMocks.follow.mockReset();
    dataMocks.useCreatedRecordLink.mockReset();
    dataMocks.useCreatedRecordLink.mockReturnValue(dataMocks.follow);
    dataMocks.useActionMutation.mockReset();
    dataMocks.useActionMutation.mockReturnValue([
      dataMocks.mutate,
      { fetching: false, error: null },
    ]);
  });

  test("reads a saved record id from the action context", () => {
    expect(recordActionId(actionContext("row-1"))).toBe("row-1");
    expect(recordActionId(actionContext(""))).toBeUndefined();
    expect(recordActionId(actionContext(undefined))).toBeUndefined();
  });

  test("action forms forward transient input and own their error feedback", () => {
    renderHook(() => useActionOutcomeMutation("replace_secret", { transient: true, errorNotification: false }));
    expect(dataMocks.useActionMutation).toHaveBeenCalledWith("replace_secret", expect.objectContaining({
      transient: true, errorNotification: false,
    }));
  });

  test("runs by record id, refreshes, and returns the action message", async () => {
    const refresh = vi.fn();
    const run = vi.fn(async () => "Synced");
    const { result } = renderHook(() => useRecordAction(run));

    let message: string | void = undefined;
    await act(async () => {
      message = await result.current(actionContext("row-1", { refresh }));
    });

    expect(run).toHaveBeenCalledWith(
      "row-1",
      expect.objectContaining({ record: { id: "row-1" } }),
    );
    expect(refresh).toHaveBeenCalledOnce();
    expect(message).toBe("Synced");
  });

  test("uses the default message when the action returns no message", async () => {
    const { result } = renderHook(() =>
      useRecordAction(async () => undefined, { defaultMessage: "Done" }),
    );

    let message: string | void = undefined;
    await act(async () => {
      message = await result.current(actionContext("row-1"));
    });

    expect(message).toBe("Done");
  });

  test("the default message stands in for an empty success message of a bound verb", async () => {
    dataMocks.mutate.mockResolvedValueOnce({ ok: true, message: "" });
    const { result } = renderHook(() =>
      useRecordActionMutation("discover_oauth_endpoints", { defaultMessage: "Discovery done." }),
    );

    let message: string | void = undefined;
    await act(async () => {
      message = await result.current[0](actionContext("cli_1"));
    });

    expect(message).toBe("Discovery done.");
  });

  test("runs afterSuccess after the form refresh", async () => {
    const calls: string[] = [];
    const { result } = renderHook(() =>
      useRecordAction(async () => "Done", {
        afterSuccess: () => {
          calls.push("afterSuccess");
        },
      }),
    );

    await act(async () => {
      await result.current(
        actionContext("row-1", {
          refresh: () => {
            calls.push("refresh");
          },
        }),
      );
    });

    expect(calls).toEqual(["refresh", "afterSuccess"]);
  });

  test("throws the configured missing-record message", async () => {
    const { result } = renderHook(() =>
      useRecordAction(async () => "Done", {
        missingRecordMessage: "Save first",
      }),
    );

    await expect(result.current(actionContext(undefined))).rejects.toThrow(
      "Save first",
    );
  });

  test("forwards the schema's record argument binding", () => {
    renderHook(() => useRecordActionMutation("open_round", { idArgument: "round" }));
    expect(dataMocks.useActionMutation).toHaveBeenCalledWith("open_round", expect.objectContaining({ idArgument: "round" }));
  });

  test("forwards that the verb's failures are recorded state", () => {
    renderHook(() => useRecordActionMutation("provision_agent", { invalidateOnFailure: true }));
    expect(dataMocks.useActionMutation).toHaveBeenCalledWith(
      "provision_agent",
      expect.objectContaining({ invalidateOnFailure: true }),
    );
  });

  test("uses the declared schema outside the resource view context", () => {
    renderHook(() => useRecordActionMutation("reprocess_workflow_run", { dataProviderName: "console" }));
    expect(dataMocks.useActionMutation).toHaveBeenCalledWith("reprocess_workflow_run", expect.objectContaining({ dataProviderName: "console" }));
  });

  test("passes invalidation targets to the data action owner", async () => {
    const refresh = vi.fn();
    const { result } = renderHook(() =>
      useRecordActionMutation("refresh_source", {
        invalidateModels: ["Skill"],
      }),
    );

    await act(async () => {
      await result.current[0](actionContext("src_1", { refresh }));
    });

    expect(dataMocks.useActionMutation).toHaveBeenCalledWith(
      "refresh_source",
      {
        invalidateModels: ["agents.Skill"],
        invalidates: [
          {
            dataProviderName: "console",
            invalidates: ["list"],
            resource: "agents.Skill",
          },
        ],
      },
    );
    expect(dataMocks.mutate).toHaveBeenCalledWith("src_1");
    expect(refresh).toHaveBeenCalledOnce();
  });

  test("settles and forwards action arguments beside the record id", async () => {
    const { result } = renderHook(() =>
      useActionResultMutation("submit_channel_password"),
    );

    await act(async () => {
      await result.current[0]("chn_1", { password: "one-use-secret" });
    });

    expect(dataMocks.mutate).toHaveBeenCalledWith("chn_1", {
      password: "one-use-secret",
    });
    expect(dataMocks.settle).toHaveBeenCalledOnce();
  });

  test("an id-returning record action resolves its message for the bar, follows the created record, then refreshes", async () => {
    const refresh = vi.fn();
    const created = { ok: true, message: "Converted", id: "doc_1" };
    dataMocks.mutate.mockResolvedValueOnce(created);
    const { result } = renderHook(() =>
      useRecordActionMutation("convert_to_document", {
        linkTo: "example.Document",
      }),
    );

    let message: string | void = undefined;
    await act(async () => {
      message = await result.current[0](actionContext("lead_1", { refresh }));
    });

    expect(dataMocks.mutate).toHaveBeenCalledWith("lead_1");
    expect(message).toBe("Converted");
    expect(dataMocks.useCreatedRecordLink).toHaveBeenLastCalledWith("example.Document");
    expect(dataMocks.follow.mock.calls).toEqual([[created]]);
    expect(refresh).toHaveBeenCalledOnce();
    expect(dataMocks.follow.mock.invocationCallOrder[0]).toBeLessThan(refresh.mock.invocationCallOrder[0]!);
  });

  test("a record action bound with the hook never calls the shared settle owner", async () => {
    const { result } = renderHook(() =>
      useRecordActionMutation("convert_to_document", { linkTo: "example.Document" }),
    );

    await act(async () => {
      await result.current[0](actionContext("lead_1"));
    });

    // The awaited run above is the effect that proves the hook ran before the negative assertions.
    expect(dataMocks.mutate).toHaveBeenCalledWith("lead_1");
    expect(dataMocks.useActionResultRun).not.toHaveBeenCalled();
    expect(dataMocks.settle).not.toHaveBeenCalled();
  });

  test("derives generated action arguments from the current record id", async () => {
    const { result } = renderHook(() =>
      useRecordActionMutation("submit_proposal", {
        actionArguments: (proposal) => ({ proposal }),
      }),
    );

    await act(async () => {
      await result.current[0](actionContext("proposal_1"));
    });

    expect(dataMocks.mutate).toHaveBeenCalledWith("proposal_1", {
      proposal: "proposal_1",
    });
  });

  test("a record action the server refuses neither refreshes nor follows", async () => {
    const refresh = vi.fn();
    const afterSuccess = vi.fn();
    dataMocks.mutate.mockResolvedValueOnce({ ok: false, message: "Refused", id: "doc_1" });
    const { result } = renderHook(() =>
      useRecordActionMutation("submit_proposal", { linkTo: "example.Document", afterSuccess }),
    );

    await expect(result.current[0](actionContext("proposal_1", { refresh }))).rejects.toThrow(new Error("Refused"));
    expect(refresh).not.toHaveBeenCalled();
    expect(afterSuccess).not.toHaveBeenCalled();
    expect(dataMocks.follow).not.toHaveBeenCalled();
  });

  test("an answer that carries no outcome rejects with the shared no-result text, and does not refresh", async () => {
    const refresh = vi.fn();
    dataMocks.mutate.mockResolvedValueOnce(undefined);
    const { result } = renderHook(() => useRecordActionMutation("submit_proposal"));

    await expect(result.current[0](actionContext("proposal_1", { refresh }))).rejects.toThrow(
      new Error("The action returned no result."),
    );
    expect(refresh).not.toHaveBeenCalled();
    expect(dataMocks.follow).not.toHaveBeenCalled();
  });

  test("projects the in-band outcome to the rendered action contract", async () => {
    const { result } = renderHook(() => useRecordActionMutation("sync_source"));

    // Success resolves the message the action bar toasts.
    let message: string | void = undefined;
    await act(async () => {
      message = await result.current[0](actionContext("src_1"));
    });
    expect(message).toBe("Synced");

    // A domain failure (ok=false) throws so the action bar surfaces the danger toast.
    dataMocks.mutate.mockResolvedValueOnce({ ok: false, message: "Sync refused." });
    await expect(result.current[0](actionContext("src_1"))).rejects.toThrow(
      new Error("Sync refused."),
    );

    // A refused target's message is only the verb's summary; its reason arrives in-band and is what is thrown.
    dataMocks.mutate.mockResolvedValueOnce({
      ok: false, message: "Sync failed.", validationErrors: { id: ["You are not allowed to sync this source."] },
    });
    await expect(result.current[0](actionContext("src_1"))).rejects.toThrow(
      new Error("You are not allowed to sync this source."),
    );
  });
});

describe("useRecordChromeActionMutation", () => {
  beforeEach(() => {
    dataMocks.mutate.mockClear();
    dataMocks.settle.mockReset();
    dataMocks.settle.mockImplementation(async (fire: () => Promise<unknown>) => {
      await fire();
      return undefined;
    });
    dataMocks.useActionResultRun.mockReset();
    dataMocks.useActionResultRun.mockReturnValue(dataMocks.settle);
    dataMocks.follow.mockReset();
    dataMocks.useCreatedRecordLink.mockReset();
    dataMocks.useCreatedRecordLink.mockReturnValue(dataMocks.follow);
    dataMocks.useActionMutation.mockReset();
    dataMocks.useActionMutation.mockReturnValue([
      dataMocks.mutate,
      { fetching: false, error: null },
    ]);
    dataMocks.chrome = {
      resource: "messaging.Channel",
      canonicalResource: "integrate.Integration",
      dataProviderName: "console",
      recordId: "chn_1",
      record: { lifecycle: "CONNECTED" },
    };
  });

  test("record chrome forwards transient input and form-owned error feedback", () => {
    renderHook(() => useRecordChromeActionOutcome("replace_secret", { transient: true, errorNotification: false }));
    expect(dataMocks.useActionMutation).toHaveBeenCalledWith("replace_secret", expect.objectContaining({
      transient: true, errorNotification: false, dataProviderName: "console",
    }));
  });

  test("invalidates the record's own model and its canonical MTI parent", async () => {
    // The three chrome call sites each re-spelled this mapping; it lives here now,
    // read off the chrome context rather than declared by each contributing addon.
    const { result } = renderHook(() =>
      useRecordChromeActionMutation("pause_integration"),
    );

    await act(async () => {
      await result.current[0]("chn_1");
    });

    expect(dataMocks.useActionMutation).toHaveBeenCalledWith("pause_integration", {
      dataProviderName: "console",
      invalidateModels: ["messaging.Channel", "integrate.Integration"],
      invalidates: [
        { dataProviderName: "console", invalidates: ["list"], resource: "messaging.Channel" },
        { dataProviderName: "console", invalidates: ["list"], resource: "integrate.Integration" },
      ],
    });
    expect(dataMocks.mutate).toHaveBeenCalledWith("chn_1");
    expect(dataMocks.settle).toHaveBeenCalledOnce();
  });

  test("names the model once when it is its own canonical parent", () => {
    dataMocks.chrome = {
      ...dataMocks.chrome,
      resource: "integrate.Integration",
      canonicalResource: "integrate.Integration",
    };
    renderHook(() => useRecordChromeActionMutation("pause_integration"));

    expect(dataMocks.useActionMutation).toHaveBeenCalledWith(
      "pause_integration",
      expect.objectContaining({
        invalidateModels: ["integrate.Integration"],
        invalidates: [
          { dataProviderName: "console", invalidates: ["list"], resource: "integrate.Integration" },
        ],
      }),
    );
  });

  test("adds a third model the verb also writes", () => {
    renderHook(() =>
      useRecordChromeActionMutation("disconnect_channel", {
        invalidateModels: ["messaging.Thread"],
      }),
    );

    expect(dataMocks.useActionMutation).toHaveBeenCalledWith(
      "disconnect_channel",
      expect.objectContaining({
        invalidateModels: [
          "messaging.Channel",
          "integrate.Integration",
          "messaging.Thread",
        ],
        invalidates: [
          { dataProviderName: "console", invalidates: ["list"], resource: "messaging.Channel" },
          { dataProviderName: "console", invalidates: ["list"], resource: "integrate.Integration" },
          { dataProviderName: "console", invalidates: ["list"], resource: "messaging.Thread" },
        ],
      }),
    );
  });

  test("routes the verb to the schema that owns the record", () => {
    // The chrome context carries the record's own data provider, so a contribution
    // never names a schema (an addon hardcoding `"console"` broke on any other).
    dataMocks.chrome = { ...dataMocks.chrome, dataProviderName: "operator" };
    renderHook(() => useRecordChromeActionMutation("pause_integration"));

    expect(dataMocks.useActionMutation).toHaveBeenCalledWith(
      "pause_integration",
      expect.objectContaining({ dataProviderName: "operator" }),
    );
  });
});

describe("useRecordChromeActionOutcome", () => {
  beforeEach(() => {
    dataMocks.authoredMutate.mockClear();
    dataMocks.useAuthoredResourceMutation.mockReset();
    dataMocks.useAuthoredResourceMutation.mockReturnValue([
      dataMocks.authoredMutate,
      { fetching: false, error: null, reset: vi.fn() },
    ]);
  });

  test("an authored verb passes a call's quiet options on to its mutation", async () => {
    // `ActionFormDialog` renders the refusal itself, so the authored call it runs
    // must turn refine's toast off as the action-field transport does.
    const { result } = renderHook(() =>
      useRecordChromeActionOutcome({
        document: {} as never,
        resultField: "sync_calendar",
        idArgument: "calendar_id",
      }),
    );

    let outcome: unknown;
    await act(async () => {
      outcome = await result.current[0]("cal_1", { full: true }, { errorNotification: false });
    });

    expect(dataMocks.authoredMutate).toHaveBeenCalledExactlyOnceWith(
      { full: true, calendar_id: "cal_1" },
      { errorNotification: false },
    );
    expect(outcome).toEqual({ ok: true, message: "Synced" });
  });
});

function actionContext(
  id: string | undefined,
  overrides: Partial<ActionContext> = {},
): ActionContext {
  return {
    record: id === undefined ? null : { id },
    values: {},
    refresh: vi.fn(),
    update: vi.fn(),
    prompt: vi.fn(),
    ...overrides,
  };
}
