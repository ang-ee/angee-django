// @vitest-environment happy-dom

import { act, cleanup, render, renderHook } from "@testing-library/react";
import { AuthoredEnvelopeError, ServerUnavailableError, boundedGraphQLTransportError } from "@angee/refine";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test } from "vitest";

import { AppRuntimeProvider, type RuntimeAuthState } from "../runtime";
import { useIntentKeys, type IntentKeys } from "./intent-key";

afterEach(cleanup);

let scopes = 0;
/** A scope of its own per test: the store lives for the page, so tests never share keys. */
function freshScope(): string {
  scopes += 1;
  return `test.scope-${scopes}`;
}

function graphQLAnswer(code: string) {
  return boundedGraphQLTransportError({ request: {}, response: { status: 200, errors: [{ message: `Refused: ${code}.`, extensions: { code } }] } });
}

function keysFor(scope: string): { current: IntentKeys; unmount: () => void } {
  const { result, unmount } = renderHook(() => useIntentKeys(scope));
  return { get current() { return result.current; }, unmount };
}

/** Send `intent` once with `answer` (a value, or an error to throw) and return the key it carried. */
async function send(keys: IntentKeys, intent: unknown, answer: unknown): Promise<string> {
  let sent = "";
  await act(async () => {
    await keys.run(intent, async (key) => {
      sent = key;
      if (answer instanceof Error) throw answer;
      return answer;
    }).catch(() => undefined);
  });
  return sent;
}

const intent = { channel: "chn_1", to: ["ada@example.com"], text: "Hello" };

describe("useIntentKeys", () => {
  test("the same intent gives the same key across an unmount and a remount, until an answer releases it", async () => {
    const scope = freshScope();
    const first = keysFor(scope);
    const lost = await send(first.current, intent, new TypeError("Failed to fetch"));
    first.unmount();
    const second = keysFor(scope);
    expect(second.current.keyFor(intent)).toBe(lost);
    expect(await send(second.current, intent, { ok: true })).toBe(lost);
    expect(second.current.keyFor(intent)).not.toBe(lost);
  });

  test("object-key order does not change the key; another value or scope does", () => {
    const scope = freshScope();
    const keys = keysFor(scope).current;
    const key = keys.keyFor({ text: "Hello", to: ["ada@example.com"], channel: "chn_1" });
    expect(keys.keyFor(intent)).toBe(key);
    expect(keys.keyFor({ ...intent, text: "Hello!" })).not.toBe(key);
    expect(keysFor(freshScope()).current.keyFor(intent)).not.toBe(key);
  });

  test("another person gets another key, and a change of person forgets the kept ones", async () => {
    const scope = freshScope();
    const auth = (id: string): RuntimeAuthState => ({ user: { id, name: id }, status: "authenticated", hasRole: () => false });
    const holder: { keys?: IntentKeys } = {};
    function Probe(): ReactNode {
      holder.keys = useIntentKeys(scope);
      return null;
    }
    const view = render(<AppRuntimeProvider runtime={{ auth: auth("usr_ana") }}><Probe /></AppRuntimeProvider>);
    const anas = await send(holder.keys!, intent, new TypeError("Failed to fetch"));
    expect(holder.keys!.state(intent)).toBe("unconfirmed");
    view.rerender(<AppRuntimeProvider runtime={{ auth: auth("usr_ben") }}><Probe /></AppRuntimeProvider>);
    expect(holder.keys!.state(intent)).toBeNull();
    expect(holder.keys!.keyFor(intent)).not.toBe(anas);
  });

  test.each<[string, unknown]>([
    ["an ok result", { ok: true, id: "msg_1" }],
    ["a refused result", { ok: false, message: "Message not sent.", validationErrors: { to: ["Not an address."] } }],
    ["an in-band envelope refusal", new AuthoredEnvelopeError("Comments are closed.", "BAD_MESSAGE")],
    ["a validation answer", graphQLAnswer("VALIDATION")],
    ["a key conflict", graphQLAnswer("CREATION_KEY_CONFLICT")],
    ["a masked server error", boundedGraphQLTransportError({ request: {}, response: { status: 200, errors: [{ message: "boom", extensions: { code: "INTERNAL" } }] } })],
  ])("%s is an answer: it releases the key", async (_name, answer) => {
    const keys = keysFor(freshScope()).current;
    const key = await send(keys, intent, answer);
    expect(keys.state(intent)).toBeNull();
    expect(keys.keyFor(intent)).not.toBe(key);
  });

  test.each<[string, Error, string]>([
    ["a lost answer (network failure)", new TypeError("Failed to fetch"), "unconfirmed"],
    ["a lost answer (bounded, no response)", boundedGraphQLTransportError(new TypeError("Failed to fetch")), "unconfirmed"],
    ["a gateway's own page", boundedGraphQLTransportError({ request: {}, response: { status: 504, error: "<html>timeout</html>" } }), "unconfirmed"],
    ["a request refused before sending", new ServerUnavailableError(), "not-sent"],
    ["a refusal of who may write", graphQLAnswer("PERMISSION_DENIED"), "denied"],
    ["an in-band refusal of who may write", new AuthoredEnvelopeError("Not allowed.", "PERMISSION_DENIED"), "denied"],
  ])("%s keeps the key with its state", async (_name, failure, state) => {
    const keys = keysFor(freshScope()).current;
    const key = await send(keys, intent, failure);
    expect(keys.state(intent)).toBe(state);
    expect(keys.keyFor(intent)).toBe(key);
  });

  test("a refusal after an unconfirmed attempt says so; a request refused before sending leaves the attempt unconfirmed", async () => {
    const keys = keysFor(freshScope()).current;
    const key = await send(keys, intent, new TypeError("Failed to fetch"));
    expect(await send(keys, intent, new ServerUnavailableError())).toBe(key);
    expect(keys.state(intent)).toBe("unconfirmed");
    expect(await send(keys, intent, graphQLAnswer("PERMISSION_DENIED"))).toBe(key);
    expect(keys.state(intent)).toBe("denied-after-unconfirmed");
  });

  test("every error is rethrown as it was; a result is returned as it was", async () => {
    const keys = keysFor(freshScope()).current;
    const failure = graphQLAnswer("PERMISSION_DENIED");
    await expect(keys.run(intent, async () => { throw failure; })).rejects.toBe(failure);
    const result = { ok: true };
    await expect(keys.run(intent, async () => result)).resolves.toBe(result);
  });

  test("an answer that lands after its surface unmounted still settles the key", async () => {
    const scope = freshScope();
    const first = keysFor(scope);
    let fail!: (error: Error) => void;
    let sent = "";
    const pending = first.current.run(intent, (key) => {
      sent = key;
      return new Promise((_resolve, reject) => { fail = reject; });
    }).catch(() => undefined);
    first.unmount();
    await act(async () => { fail(new TypeError("Failed to fetch")); await pending; });
    const second = keysFor(scope).current;
    expect(second.state(intent)).toBe("unconfirmed");
    expect(second.keyFor(intent)).toBe(sent);
  });

  test("two sends of one intent before either answers carry one key; the first answer releases it, the second changes nothing", async () => {
    const keys = keysFor(freshScope()).current;
    const answers: Array<(value: unknown) => void> = [];
    const carried: string[] = [];
    const fire = (key: string) => { carried.push(key); return new Promise((resolve) => { answers.push(resolve); }); };
    const both = Promise.all([keys.run(intent, fire), keys.run(intent, fire)]);
    expect(carried[0]).toBe(carried[1]);
    await act(async () => { answers[0]!({ ok: true }); await Promise.resolve(); });
    const next = keys.keyFor(intent);
    expect(next).not.toBe(carried[0]);
    await act(async () => { answers[1]!({ ok: true }); await both; });
    expect(keys.keyFor(intent)).toBe(next);
  });

  test("the store holds at most a hundred intents, forgetting the oldest first", () => {
    const keys = keysFor(freshScope()).current;
    const oldest = keys.keyFor({ n: 0 });
    const second = keys.keyFor({ n: 1 });
    for (let n = 2; n <= 100; n += 1) keys.keyFor({ n });
    expect(keys.keyFor({ n: 1 })).toBe(second);
    expect(keys.keyFor({ n: 0 })).not.toBe(oldest);
  });

  test("a settled key re-renders the surface reading its state", async () => {
    const scope = freshScope();
    const seen: Array<string | null> = [];
    function Note(): ReactNode {
      const keys = useIntentKeys(scope);
      seen.push(keys.state(intent));
      return null;
    }
    render(<Note />);
    const keys = keysFor(scope).current;
    await send(keys, intent, new TypeError("Failed to fetch"));
    expect(seen.at(-1)).toBe("unconfirmed");
  });
});
