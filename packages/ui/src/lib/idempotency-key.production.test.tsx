// @vitest-environment happy-dom

import { act, cleanup, render } from "@testing-library/react";
import { Component, type ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../runtime";
import { useIdempotencyDraftOwner } from "./idempotency-key";

// A production build: the development check never throws.
vi.mock("./development-mode", () => ({ developmentMode: () => false }));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

class Caught extends Component<{ children?: ReactNode; caught: (error: Error) => void }, { failed: boolean }> {
  override state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  override componentDidCatch(error: Error) { this.props.caught(error); }
  override render() { return this.state.failed ? null : this.props.children; }
}

function Holder({ scope, name }: { scope: string; name: string }): ReactNode {
  useIdempotencyDraftOwner(scope, name);
  return null;
}

test("in a production build two holders of one scope never take the page down: one console error per scope, naming both", async () => {
  const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const errors: Error[] = [];
  const wrap = (node: ReactNode) => <AppRuntimeProvider runtime={{}}><Caught caught={(error) => errors.push(error)}>{node}</Caught></AppRuntimeProvider>;
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(wrap(<><Holder scope="box" name="one box" /><Holder scope="box" name="another box" /></>)); });
  await act(async () => {
    view.rerender(wrap(<><Holder scope="box" name="one box" /><Holder scope="box" name="another box" /><Holder scope="box" name="a third box" /></>));
  });
  expect(errors).toEqual([]);
  expect(logged).toHaveBeenCalledOnce();
  expect(String(logged.mock.calls[0]?.[0])).toContain('scope "box": one box and another box');
});
