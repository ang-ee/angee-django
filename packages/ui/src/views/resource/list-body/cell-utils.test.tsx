// @vitest-environment happy-dom

import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { ModelMetadata } from "@angee/metadata";
import type { UiTranslate } from "../../../i18n";
import { cellContent, formatMeasure } from "./cell-utils";

afterEach(cleanup);

const metadata = {
  fields: { status: { kind: "enum", values: [{ value: "open", description: "In progress" }] } },
} as unknown as ModelMetadata;
const t = ((key: string) => key) as UiTranslate;

test("enum metadata labels both plain cells and toned badges across GraphQL casing", () => {
  const plain = cellContent({ field: "status" }, { status: "OPEN" }, t, metadata);
  expect(plain).toBe("In progress");
  render(<>{cellContent({ field: "status", tone: { OPEN: "info" } }, { status: "OPEN" }, t, metadata)}</>);
  expect(screen.getByText("In progress")).toBeTruthy();
});

test("measures format numeric values and preserve other strings", () => {
  expect(formatMeasure(1234.5678, { unit: "ms" })).toBe("1,234.568 ms");
  expect(formatMeasure("12345678901234567890", { unit: "" })).toBe("12,345,678,901,234,567,890");
  expect(formatMeasure("unknown", { unit: "" })).toBe("unknown");
});
