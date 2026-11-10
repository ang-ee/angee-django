// @vitest-environment happy-dom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { AppRuntimeProvider } from "../runtime";
import { afterEach, describe, expect, test, vi } from "vitest";

import { displayMime, normaliseMime } from "./model";
import { PreviewPane } from "./PreviewPane";
import { resolvePreviewProvider, type PreviewProvider } from "./registry";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

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

  test.each([
    ["text/xml", "<root><value>one very long XML value</value></root>", "<root><value>one very long XML value</value></root>"],
    ["application/json", '{"answer":42}', '{\n  "answer": 42\n}'],
  ])("formats and wraps %s text previews", async (mime, body, expected) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200 })));
    const rendered = render(
      <PreviewPane file={{ url: "/document", name: "document", mime }} />,
    );

    await waitFor(() => expect(rendered.container.querySelector("pre")?.textContent).toBe(expected));
    expect(rendered.container.querySelector("pre")?.className).toContain("whitespace-pre-wrap");
    expect(rendered.container.querySelector("pre")?.className).toContain("break-words");
  });
});

test("preview identity preserves a renderer and its loaded URL across token rotation", () => {
  const renderedUrls: string[] = [];
  const provider: PreviewProvider = {
    id: "document", mime: "application/pdf",
    component: ({ file, page }) => {
      renderedUrls.push(file.url);
      return <input aria-label="Document state" defaultValue={`${file.name}:${page}`} />;
    },
  };
  const file = { identity: { id: "file-1", version: "sha256-a" }, url: "/file?token=first", name: "one.pdf", mime: "application/pdf" };
  const pane = (value: typeof file, page = 1) => <AppRuntimeProvider runtime={{ previews: [provider] }}>
    <PreviewPane file={value} page={page} />
  </AppRuntimeProvider>;
  const view = render(pane(file));
  const input = screen.getByRole("textbox") as HTMLInputElement;
  input.value = "retained page/canvas state";
  view.rerender(pane({ ...file, url: "/file?token=second" }, 3));
  expect(screen.getByRole("textbox")).toBe(input);
  expect(input.value).toBe("retained page/canvas state");
  expect(renderedUrls.at(-1)).toBe("/file?token=first");
  view.rerender(pane({ ...file, identity: { ...file.identity, version: "sha256-b" }, url: "/file?token=new-content" }));
  expect(screen.getByRole("textbox")).not.toBe(input);
  expect(renderedUrls.at(-1)).toBe("/file?token=new-content");
  const second = screen.getByRole("textbox");
  view.rerender(pane({ ...file, identity: { id: "file-2", version: "sha256-b" }, url: "/other" }));
  expect(screen.getByRole("textbox")).not.toBe(second);
  expect(renderedUrls.at(-1)).toBe("/other");
});

test.each(["image/png", "text/plain"])("%s keeps loaded bytes when only credentials change", async (mime) => {
  const fetch = vi.fn(async () => new Response("document"));
  vi.stubGlobal("fetch", fetch);
  const file = { identity: { id: "file-1", version: "digest" }, url: "/first", name: "one", mime };
  const view = render(<PreviewPane file={file} />);
  if (mime === "text/plain") await waitFor(() => expect(view.container.querySelector("pre")?.textContent).toBe("document"));
  const image = view.container.querySelector("img");
  view.rerender(<PreviewPane file={{ ...file, url: "/second" }} />);
  if (mime === "image/png") {
    expect(view.container.querySelector("img")).toBe(image);
    expect(image?.getAttribute("src")).toBe("/first");
  } else expect(fetch).toHaveBeenCalledTimes(1);
});
