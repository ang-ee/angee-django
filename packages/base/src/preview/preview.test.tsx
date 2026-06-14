// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { AppRuntimeProvider } from "@angee/sdk";
import { afterEach, describe, expect, test } from "vitest";

import { displayMime, normaliseMime } from "./model";
import { PreviewPane } from "./PreviewPane";
import { resolvePreviewProvider, type PreviewProvider } from "./registry";

afterEach(cleanup);

describe("preview model", () => {
  test("displayMime falls back to the extension then octet-stream", () => {
    expect(displayMime({ url: "/a", name: "note.md" })).toBe("text/markdown");
    expect(displayMime({ url: "/a", name: "pic.png", mime: "image/png" })).toBe(
      "image/png",
    );
    expect(normaliseMime("text/plain; charset=utf-8")).toBe("text/plain");
  });
});

describe("resolvePreviewProvider", () => {
  const Img: PreviewProvider["component"] = () => <span>img</span>;
  const Any: PreviewProvider["component"] = () => <span>any</span>;
  const providers: readonly PreviewProvider[] = [
    { id: "img", mime: "image/*", component: Img },
    { id: "any", mime: "*/*", component: Any, priority: -10 },
  ];

  test("resolves by glob and priority; */* is the lowest fallback", () => {
    expect(resolvePreviewProvider(providers, "image/png")?.id).toBe("img");
    expect(resolvePreviewProvider(providers, "application/zip")?.id).toBe("any");
  });

  test("returns null when nothing matches", () => {
    expect(resolvePreviewProvider([providers[0]!], "application/zip")).toBeNull();
  });

  test("an earlier entry wins an equal-priority tie (addon overrides built-in)", () => {
    const Override: PreviewProvider["component"] = () => <span>override</span>;
    // Same (default) priority: the addon is listed first, stable sort keeps it.
    const list: readonly PreviewProvider[] = [
      { id: "addon.image", mime: "image/*", component: Override },
      { id: "base.image", mime: "image/*", component: Img },
    ];
    expect(resolvePreviewProvider(list, "image/png")?.id).toBe("addon.image");
  });
});

describe("PreviewPane", () => {
  test("renders a built-in renderer for a known mime (no runtime needed)", () => {
    render(
      <PreviewPane file={{ url: "/x.png", name: "x.png", mime: "image/png" }} />,
    );
    // The built-in image renderer shows an <img> with the file name as alt text.
    expect(screen.getByRole("img", { name: "x.png" })).toBeTruthy();
  });

  test("an addon-contributed runtime provider overrides the built-in", () => {
    const addonImage: PreviewProvider = {
      id: "addon.image",
      mime: "image/*",
      component: ({ file }) => <span>addon: {file.name}</span>,
      priority: 5,
    };
    render(
      <AppRuntimeProvider runtime={{ previews: [addonImage] }}>
        <PreviewPane file={{ url: "/x.png", name: "x.png", mime: "image/png" }} />
      </AppRuntimeProvider>,
    );
    expect(screen.getByText("addon: x.png")).toBeTruthy();
  });

  test("the generic built-in fallback handles an unknown binary mime", () => {
    render(
      <PreviewPane
        file={{ url: "/x.bin", name: "x.bin", mime: "application/zip" }}
      />,
    );
    // base.fallback renders an EmptyState titled with the file name.
    expect(screen.getByText("x.bin")).toBeTruthy();
  });
});
