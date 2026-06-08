// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { displayMime, normaliseMime } from "./model";
import { PreviewPane } from "./PreviewPane";
import {
  clearPreviewProvidersForTest,
  registerPreviewProvider,
  resolvePreviewProvider,
} from "./registry";

afterEach(cleanup);
beforeEach(clearPreviewProvidersForTest);

describe("preview model", () => {
  test("displayMime falls back to the extension then octet-stream", () => {
    expect(displayMime({ url: "/a", name: "note.md" })).toBe("text/markdown");
    expect(displayMime({ url: "/a", name: "pic.png", mime: "image/png" })).toBe(
      "image/png",
    );
    expect(normaliseMime("text/plain; charset=utf-8")).toBe("text/plain");
  });
});

describe("preview registry", () => {
  test("resolves by glob and priority; */* is the lowest fallback", () => {
    const Img = () => <span>img</span>;
    const Any = () => <span>any</span>;
    registerPreviewProvider({ id: "img", mime: "image/*", component: Img });
    registerPreviewProvider({ id: "any", mime: "*/*", component: Any, priority: -10 });
    expect(resolvePreviewProvider("image/png")?.id).toBe("img");
    expect(resolvePreviewProvider("application/zip")?.id).toBe("any");
  });
});

describe("PreviewPane", () => {
  test("renders the resolved provider", () => {
    registerPreviewProvider({
      id: "img",
      mime: "image/*",
      component: ({ file }) => <span>shown: {file.name}</span>,
    });
    render(<PreviewPane file={{ url: "/x.png", name: "x.png", mime: "image/png" }} />);
    expect(screen.getByText("shown: x.png")).toBeTruthy();
  });

  test("falls back when no provider matches", () => {
    render(<PreviewPane file={{ url: "/x.bin", name: "x.bin", mime: "application/zip" }} />);
    expect(screen.getByText("No preview available")).toBeTruthy();
  });
});
