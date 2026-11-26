// @vitest-environment happy-dom

import { act, cleanup, render, renderHook } from "@testing-library/react";
import { AuthoredEnvelopeError, ServerUnavailableError, boundedGraphQLTransportError, type FailedWriteOutcome } from "@angee/refine";
import { Component, StrictMode, type ReactNode } from "react";
import { afterEach, describe, expect, test } from "vitest";

import { AppRuntimeProvider, type RuntimeAuthState, type RuntimeViewAs } from "../runtime";
import {
  DraftScopeProvider, useDraftScope, useIdempotencyDraftOwner, useIdempotencyKeys, useIdempotencyKeyState, type IdempotencyKeys,
} from "./idempotency-key";

afterEach(cleanup);

const answered = (status: number, errors: readonly { code: string }[], data?: unknown) => boundedGraphQLTransportError({
  request: {}, response: { status, ...(data === undefined ? {} : { data }), errors: errors.map(({ code }) => ({ message: `Refused: ${code}.`, extensions: { code } })) },
});

/** Keys under one app runtime, as a page has them. */
function keysUnderRuntime(): { current: IdempotencyKeys; state: (scope: string) => ReturnType<typeof useIdempotencyKeyState> } {
  const states: Record<string, ReturnType<typeof useIdempotencyKeyState>> = {};
  let keys!: IdempotencyKeys;
  const scopes = new Set<string>();
  function Probe({ watch }: { watch: readonly string[] }): ReactNode {
    keys = useIdempotencyKeys();
    return <>{watch.map((scope) => <Watch key={scope} scope={scope} />)}</>;
  }
  function Watch({ scope }: { scope: string }): ReactNode {
    states[scope] = useIdempotencyKeyState(scope);
    return null;
  }
  const view = render(<AppRuntimeProvider runtime={{}}><Probe watch={[]} /></AppRuntimeProvider>);
  return {
    get current() { return keys; },
    state: (scope) => {
      if (!scopes.has(scope)) {
        scopes.add(scope);
        view.rerender(<AppRuntimeProvider runtime={{}}><Probe watch={[...scopes]} /></AppRuntimeProvider>);
      }
      return states[scope] ?? null;
    },
  };
}

/** Send the draft `scope` once with `answer` (a value, or an error to throw) and return the key it carried. */
async function send(keys: IdempotencyKeys, scope: string, answer: unknown, outcome?: (answer: unknown) => FailedWriteOutcome | null): Promise<string> {
  let sent = "";
  await act(async () => {
    await keys.send(scope, async (key) => {
      sent = key;
      if (answer instanceof Error) throw answer;
      return answer;
    }, outcome).catch(() => undefined);
  });
  return sent;
}

describe("useIdempotencyKeys", () => {
  test.each<[string, unknown, FailedWriteOutcome, boolean]>([
    ["a network failure", new TypeError("Failed to fetch"), "unconfirmed", true],
    ["a network failure, bounded", boundedGraphQLTransportError(new TypeError("Failed to fetch")), "unconfirmed", true],
    ["a cancelled request", boundedGraphQLTransportError(new DOMException("aborted", "AbortError")), "unconfirmed", true],
    ["a gateway's own page", boundedGraphQLTransportError({ request: {}, response: { status: 504, error: "<html>timeout</html>" } }), "unconfirmed", true],
    ["a 401 page", boundedGraphQLTransportError({ request: {}, response: { status: 401, error: "<html>sign in</html>" } }), "unconfirmed", true],
    ["a request the connection boundary refused before sending", new ServerUnavailableError(), "not-sent", false],
    ["a masked server error", answered(200, [{ code: "INTERNAL" }]), "unconfirmed", true],
    ["partial data beside a masked error", answered(200, [{ code: "INTERNAL" }], { post: { ok: true } }), "unconfirmed", true],
    ["a 500 with an errors body", answered(500, [{ code: "INTERNAL" }]), "unconfirmed", true],
    ["a 429 with an errors body", answered(429, [{ code: "THROTTLED" }]), "unconfirmed", true],
    ["a validation answer", answered(200, [{ code: "VALIDATION" }]), "refused", false],
    ["a bad input answer", answered(200, [{ code: "BAD_USER_INPUT" }]), "refused", false],
    ["a stale revision", answered(200, [{ code: "STALE_REVISION" }]), "refused", false],
    ["an in-band envelope refusal", new AuthoredEnvelopeError("Comments are closed.", "BAD_MESSAGE"), "refused", false],
    ["an in-band refusal of who may write", new AuthoredEnvelopeError("Not allowed.", "PERMISSION_DENIED"), "denied", false],
    ["a permission refusal", answered(200, [{ code: "PERMISSION_DENIED" }]), "denied", false],
    ["a forbidden answer", answered(403, [{ code: "FORBIDDEN" }]), "denied", false],
    ["a sign-in answer", answered(200, [{ code: "UNAUTHENTICATED" }]), "denied", false],
    ["a preview's refusal", answered(200, [{ code: "VIEW_AS_READ_ONLY" }]), "denied", false],
  ])("%s keeps the draft's key, and says what it told", async (_name, failure, outcome, uncertain) => {
    const keys = keysUnderRuntime();
    const first = await send(keys.current, "chatter:task-1", failure);
    expect(keys.state("chatter:task-1")).toEqual({ outcome, uncertain });
    expect(await send(keys.current, "chatter:task-1", { ok: true })).toBe(first);
  });

  test("a result its owner reads as refused keeps the key; a confirmed answer ends the draft", async () => {
    const keys = keysUnderRuntime();
    const read = (answer: unknown) => ((answer as { ok: boolean }).ok ? null : "refused" as const);
    const first = await send(keys.current, "clone:vlt_1", { ok: false, message: "Name is taken." }, read);
    expect(keys.state("clone:vlt_1")).toEqual({ outcome: "refused", uncertain: false });
    expect(await send(keys.current, "clone:vlt_1", { ok: true }, read)).toBe(first);
    expect(keys.state("clone:vlt_1")).toBeNull();
    expect(await send(keys.current, "clone:vlt_1", { ok: true }, read)).not.toBe(first);
  });

  test("a returned answer the owner reads as a conflict ends the draft at its next send, as a thrown one does: no caller rethrows", async () => {
    const keys = keysUnderRuntime();
    const read = (answer: unknown) => (answer as { outcome: FailedWriteOutcome | null }).outcome;
    const lost = await send(keys.current, "dialog:returned", new TypeError("Failed to fetch"));
    expect(await send(keys.current, "dialog:returned", { outcome: "conflict" }, read)).toBe(lost);
    expect(keys.state("dialog:returned")).toEqual({ outcome: "conflict", uncertain: true });
    const next = await send(keys.current, "dialog:returned", { outcome: null }, read);
    expect(next).not.toBe(lost);
    expect(keys.state("dialog:returned")).toBeNull();
  });

  test("a lost send, an edit and a resend carry the same key: the draft is the key's owner, not its content", async () => {
    const keys = keysUnderRuntime();
    let content = "Hi";
    const sent: Array<[string, string]> = [];
    const post = (answer: unknown) => act(async () => {
      await keys.current.send("reply:thread-1", async (key) => {
        sent.push([key, content]);
        if (answer instanceof Error) throw answer;
        return answer;
      }).catch(() => undefined);
    });
    await post(new TypeError("Failed to fetch"));
    content = "Hi!";
    await post({ ok: true });
    expect(sent).toEqual([[sent[0]![0], "Hi"], [sent[0]![0], "Hi!"]]);
  });

  test("a refusal after a lost attempt keeps saying the attempt may have been written", async () => {
    const keys = keysUnderRuntime();
    const first = await send(keys.current, "compose", new TypeError("Failed to fetch"));
    expect(await send(keys.current, "compose", answered(200, [{ code: "PERMISSION_DENIED" }]))).toBe(first);
    expect(keys.state("compose")).toEqual({ outcome: "denied", uncertain: true });
    expect(await send(keys.current, "compose", answered(200, [{ code: "VALIDATION" }]))).toBe(first);
    expect(keys.state("compose")).toEqual({ outcome: "refused", uncertain: true });
  });

  test.each([
    ["the server's answer", answered(200, [{ code: "CREATION_KEY_CONFLICT" }])],
    ["an in-band answer", new AuthoredEnvelopeError("An earlier attempt already created it.", "CREATION_KEY_CONFLICT")],
  ])("a spent key (%s) says so until the draft's next send, which starts a new draft with a new key", async (_name, conflict) => {
    const keys = keysUnderRuntime();
    // A lost create that was written, an edit sent under its key (conflict), then the next send.
    const lost = await send(keys.current, "dialog:create", new TypeError("Failed to fetch"));
    expect(await send(keys.current, "dialog:create", conflict)).toBe(lost);
    expect(keys.state("dialog:create")).toEqual({ outcome: "conflict", uncertain: true });
    const next = await send(keys.current, "dialog:create", new TypeError("Failed to fetch"));
    expect(next).not.toBe(lost);
    // The new draft is its own: lost too, it keeps its key through a dismissal, until accepted.
    act(() => { keys.current.discard("dialog:create"); });
    expect(await send(keys.current, "dialog:create", { ok: true })).toBe(next);
    expect(keys.state("dialog:create")).toBeNull();
  });

  test("a draft given up with nothing possibly written ends; one given up after a lost attempt keeps its key", async () => {
    const keys = keysUnderRuntime();
    const refused = await send(keys.current, "dialog:a", answered(200, [{ code: "VALIDATION" }]));
    act(() => { keys.current.discard("dialog:a"); });
    expect(await send(keys.current, "dialog:a", { ok: true })).not.toBe(refused);
    const lost = await send(keys.current, "dialog:b", new TypeError("Failed to fetch"));
    act(() => { keys.current.discard("dialog:b"); });
    expect(await send(keys.current, "dialog:b", new TypeError("Failed to fetch"))).toBe(lost);
  });

  test("a draft with a send on its way may be written: it is neither given up nor dropped until answered", async () => {
    const keys = keysUnderRuntime();
    let answer!: (error: Error) => void;
    let carried = "";
    let inFlight!: Promise<unknown>;
    act(() => {
      inFlight = keys.current.send("box:pending", (key) => {
        carried = key;
        return new Promise((_resolve, reject) => { answer = reject; });
      }).catch(() => undefined);
    });
    act(() => { keys.current.discard("box:pending"); });
    for (let n = 0; n <= 100; n += 1) await send(keys.current, `refused-${n}`, answered(200, [{ code: "VALIDATION" }]));
    await act(async () => { answer(new TypeError("Failed to fetch")); await inFlight; });
    expect(await send(keys.current, "box:pending", { ok: true })).toBe(carried);
  });

  test("the key is minted at the draft's first attempt: a draft never sent holds none", async () => {
    const keys = keysUnderRuntime();
    expect(keys.state("form:new")).toBeNull();
    act(() => { keys.current.discard("form:new"); });
    const first = await send(keys.current, "form:new", new TypeError("Failed to fetch"));
    expect(first).toEqual(expect.any(String));
  });

  test("two sends of one draft before either answers carry one key; the first confirmed answer ends it, the second changes nothing", async () => {
    const keys = keysUnderRuntime();
    const answers: Array<(value: unknown) => void> = [];
    const carried: string[] = [];
    const fire = (key: string) => { carried.push(key); return new Promise((resolve) => { answers.push(resolve); }); };
    let both!: Promise<unknown>;
    act(() => { both = Promise.all([keys.current.send("box", fire), keys.current.send("box", fire)]); });
    expect(carried[0]).toBe(carried[1]);
    await act(async () => { answers[0]!({ ok: true }); await Promise.resolve(); });
    const next = await send(keys.current, "box", new TypeError("Failed to fetch"));
    expect(next).not.toBe(carried[0]);
    await act(async () => { answers[1]!({ ok: true }); await both; });
    expect(keys.state("box")).toEqual({ outcome: "unconfirmed", uncertain: true });
  });

  test("every error is rethrown as it was; an answer is returned as it was", async () => {
    const keys = keysUnderRuntime();
    const failure = answered(200, [{ code: "PERMISSION_DENIED" }]);
    await expect(keys.current.send("x", async () => { throw failure; })).rejects.toBe(failure);
    const result = { ok: true };
    await expect(keys.current.send("x", async () => result)).resolves.toBe(result);
  });

  test("two app runtimes do not share keys; a provider inside another shares its runtime's", async () => {
    let outer!: IdempotencyKeys; let inner!: IdempotencyKeys; let other!: IdempotencyKeys;
    function Take({ set }: { set: (keys: IdempotencyKeys) => void }): ReactNode { set(useIdempotencyKeys()); return null; }
    render(<>
      <AppRuntimeProvider runtime={{}}>
        <Take set={(keys) => { outer = keys; }} />
        <AppRuntimeProvider runtime={{}}><Take set={(keys) => { inner = keys; }} /></AppRuntimeProvider>
      </AppRuntimeProvider>
      <AppRuntimeProvider runtime={{}}><Take set={(keys) => { other = keys; }} /></AppRuntimeProvider>
    </>);
    const kept = await send(outer, "scope", new TypeError("Failed to fetch"));
    expect(await send(inner, "scope", new TypeError("Failed to fetch"))).toBe(kept);
    expect(await send(other, "scope", new TypeError("Failed to fetch"))).not.toBe(kept);
  });

  test("a change of signed-in person forgets every key; entering a preview does not", async () => {
    const person = (id: string, viewAs?: RuntimeViewAs): RuntimeAuthState => ({
      user: { id, name: id }, status: "authenticated", hasRole: () => false, ...(viewAs ? { viewAs } : {}),
    });
    const preview = (realId: string): RuntimeViewAs => ({
      viewAs: { userId: "usr_ben" }, currentUser: { id: "usr_ben", name: "Ben" }, realUser: { id: realId, name: realId },
      viewablePeople: [], enter: () => undefined, exit: () => undefined,
    });
    let keys!: IdempotencyKeys;
    function Take(): ReactNode { keys = useIdempotencyKeys(); return null; }
    const shell = (auth: RuntimeAuthState) => <AppRuntimeProvider runtime={{}}><AppRuntimeProvider runtime={{ auth }}><Take /></AppRuntimeProvider></AppRuntimeProvider>;
    const view = render(shell(person("usr_ana")));
    const anas = await send(keys, "box", new TypeError("Failed to fetch"));
    view.rerender(shell(person("usr_ben", preview("usr_ana"))));
    expect(await send(keys, "box", new TypeError("Failed to fetch"))).toBe(anas);
    view.rerender(shell(person("usr_cleo")));
    expect(await send(keys, "box", new TypeError("Failed to fetch"))).not.toBe(anas);
  });

  test("a host that reads a draft's state re-renders when it changes; one that does not, or reads another, does not", async () => {
    const renders = { reader: 0, other: 0, sender: 0 };
    let keys!: IdempotencyKeys;
    function Sender(): ReactNode { keys = useIdempotencyKeys(); renders.sender += 1; return null; }
    function Reader({ scope, count }: { scope: string; count: "reader" | "other" }): ReactNode { useIdempotencyKeyState(scope); renders[count] += 1; return null; }
    render(<AppRuntimeProvider runtime={{}}><Sender /><Reader scope="mine" count="reader" /><Reader scope="theirs" count="other" /></AppRuntimeProvider>);
    const before = { ...renders };
    await send(keys, "mine", new TypeError("Failed to fetch"));
    expect(renders.reader).toBeGreaterThan(before.reader);
    expect(renders.other).toBe(before.other);
    expect(renders.sender).toBe(before.sender);
  });

  test("the store keeps a hundred settled drafts, dropping the oldest, and never drops one that may have been written", async () => {
    const keys = keysUnderRuntime();
    const lost = await send(keys.current, "lost", new TypeError("Failed to fetch"));
    const firstRefused = await send(keys.current, "refused-0", answered(200, [{ code: "VALIDATION" }]));
    for (let n = 1; n <= 100; n += 1) await send(keys.current, `refused-${n}`, answered(200, [{ code: "VALIDATION" }]));
    expect(await send(keys.current, "lost", new TypeError("Failed to fetch"))).toBe(lost);
    expect(await send(keys.current, "refused-0", answered(200, [{ code: "VALIDATION" }]))).not.toBe(firstRefused);
  });

  test("a keyed write sent outside any app runtime says what it needs", async () => {
    const { result } = renderHook(() => useIdempotencyKeys());
    await expect(result.current.send("x", async () => "ok")).rejects.toThrow("useIdempotencyKeys must be used under AppRuntimeProvider.");
  });
});

/** Catch what a surface throws as it renders, as an app's error boundary does. */
class Caught extends Component<{ children?: ReactNode; caught: (error: Error) => void }, { failed: boolean }> {
  override state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  override componentDidCatch(error: Error) { this.props.caught(error); }
  override render() { return this.state.failed ? null : this.props.children; }
}

function Holder({ scope }: { scope: string | null }): ReactNode {
  useIdempotencyDraftOwner(scope);
  return null;
}

describe("useIdempotencyDraftOwner", () => {
  async function mount(tree: ReactNode): Promise<{ errors: Error[]; rerender: (next: ReactNode) => Promise<void> }> {
    const errors: Error[] = [];
    const wrap = (node: ReactNode) => <AppRuntimeProvider runtime={{}}><Caught caught={(error) => errors.push(error)}>{node}</Caught></AppRuntimeProvider>;
    let view!: ReturnType<typeof render>;
    await act(async () => { view = render(wrap(tree)); await Promise.resolve(); });
    return { errors, rerender: async (next) => { await act(async () => { view.rerender(wrap(next)); await Promise.resolve(); }); } };
  }

  test("two mounted surfaces holding one scope fail in development, naming it", async () => {
    const { errors } = await mount(<><Holder scope="record.create:default:tasks.Task" /><Holder scope="record.create:default:tasks.Task" /></>);
    expect(errors[0]?.message).toContain('Two mounted surfaces hold the idempotency draft scope "record.create:default:tasks.Task"');
  });

  test("a strict-mode remount, a surface replacing its predecessor in one commit, or a surface holding none is one holder", async () => {
    const view = await mount(<StrictMode><Holder key="first" scope="box" /><Holder scope={null} /><Holder scope={null} /></StrictMode>);
    await view.rerender(<StrictMode><Holder key="next" scope="box" /><Holder scope={null} /><Holder scope={null} /></StrictMode>);
    expect(view.errors).toEqual([]);
  });
});

describe("useDraftScope", () => {
  test("a surface's scope is its own under the scope its host names; a provider naming none keeps its host's", () => {
    const scopes: string[] = [];
    function Probe({ own }: { own: string }): ReactNode { scopes.push(useDraftScope(own)); return null; }
    function Host({ own, children }: { own: string; children?: ReactNode }): ReactNode {
      return <DraftScopeProvider scope={useDraftScope(own)}>{children}</DraftScopeProvider>;
    }
    render(<>
      <Probe own="record.create:default:tasks.Task" />
      <Host own="record.create:default:tasks.Task">
        <Host own="relation"><Probe own="record.create:default:tasks.Task" /></Host>
        <DraftScopeProvider scope={null}><Probe own="record.create:default:tags.Tag" /></DraftScopeProvider>
      </Host>
    </>);
    expect(scopes).toEqual([
      "record.create:default:tasks.Task",
      "record.create:default:tasks.Task/relation/record.create:default:tasks.Task",
      "record.create:default:tasks.Task/record.create:default:tags.Tag",
    ]);
  });
});
