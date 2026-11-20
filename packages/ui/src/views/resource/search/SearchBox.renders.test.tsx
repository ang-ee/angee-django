// @vitest-environment happy-dom
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SearchBox } from "./SearchBox";
import { searchFixture } from "./search-fixture.test-support";

// A test-owned spy over Base UI's combobox: the controlled value and items the box
// hands its root, and every render of the input part. The input's render callback
// runs inside the part's own render, so a render its store drives alone counts too.
const seen = vi.hoisted(() => ({ values: [] as unknown[], items: [] as unknown[], inputRenders: 0 }));
vi.mock("@base-ui/react/combobox", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@base-ui/react/combobox")>();
  const React = await import("react");
  function Root(props: React.ComponentProps<typeof actual.Combobox.Root>): React.ReactElement {
    seen.values.push(props.value);
    seen.items.push(props.items);
    return React.createElement(actual.Combobox.Root, props);
  }
  const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<typeof actual.Combobox.Input>>(function Input(props, ref) {
    return React.createElement(actual.Combobox.Input, { ...props, ref, render: (htmlProps: React.ComponentProps<"input">) => {
      seen.inputRenders += 1;
      return React.createElement("input", htmlProps);
    } });
  });
  return { ...actual, Combobox: { ...actual.Combobox, Root, Input } };
});

afterEach(cleanup);

test("re-rendering the box with the search unchanged hands the combobox the same value and items, and renders the input once", () => {
  const search = searchFixture({ active: [
    { id: "filter:mine", kind: "filter", label: "Mine" },
    { id: "text:title", kind: "text", field: "title", label: "Title", value: "ab" },
  ] });
  const view = render(<SearchBox search={search} />);
  const rendered = { roots: seen.values.length, inputs: seen.inputRenders };
  view.rerender(<SearchBox search={search} />);
  expect(seen.values.length).toBe(rendered.roots + 1);
  expect(seen.values.at(-1)).toBe(search.active);
  expect(seen.items.at(-1)).toBe(seen.items.at(-2));
  // The parent's render reaches the input once; Base UI's store has nothing new to sync.
  expect(seen.inputRenders).toBe(rendered.inputs + 1);
});
