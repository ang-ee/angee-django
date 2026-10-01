// @vitest-environment happy-dom
import { cleanup, renderHook } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test } from "vitest";

import { createUiTestProviders } from "../../testing";
import { useEnumValueLabel } from "./enum-options";

const { Provider, clearClients } = createUiTestProviders({ resources: [
  testDataResource("notes.Note", { fields: [{ name: "status", kind: "enum", readable: true,
    values: [{ value: "WAITING", description: "Awaiting review" }, { value: "IN_PROGRESS" }],
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
  }] }),
  testDataResource("notes.Revision", { fields: [{ name: "status", kind: "enum", readable: true,
    values: [{ value: "WAITING", description: "Awaiting publication" }],
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
  }] }),
] });
afterEach(() => { cleanup(); clearClients(); });

test("enum labels follow model metadata and retain readable fallbacks", () => {
  const { result, rerender } = renderHook(({ model }) => useEnumValueLabel(model), {
    initialProps: { model: "notes.Note" }, wrapper: Provider,
  });
  expect(result.current("status", "WAITING")).toBe("Awaiting review");
  expect(result.current("status", "IN_PROGRESS")).toBe("In Progress");
  expect(result.current("unknown", "UNLISTED_VALUE")).toBe("Unlisted Value");
  for (const absent of [null, undefined, ""]) expect(result.current("status", absent)).toBe("");
  rerender({ model: "notes.Revision" });
  expect(result.current("status", "WAITING")).toBe("Awaiting publication");
});
