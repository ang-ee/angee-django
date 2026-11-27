// @vitest-environment happy-dom

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useMemo } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../../runtime/runtime";
import { AppearanceProvider } from "../../theme/appearance";
import { defineTheme } from "../../theme/runtime.mjs";
import { MessageHtmlFrame, MessagePartsView, type MessagePartFile } from "./index";

function fileUrl(file: MessagePartFile): string {
  return `/files/${file.id}`;
}

describe("MessagePartsView", () => {
  test("reveals an addressed quote and keeps original-file preview separate from backlinks", () => {
    const preview = vi.fn();
    render(<MessagePartsView activePartId="quote" resolveFileUrl={fileUrl} onPreviewFile={preview}
      renderPartActions={part => part.file ? <button>Other uses</button> : null}
      parts={[{ id: "quote", role: "QUOTED", fragment: { text: "Addressed context" } },
        { id: "file", name: "original.pdf", disposition: "ATTACHMENT", file: { id: "shared", filename: "canonical.pdf" } }]} />);
    expect(screen.getByText("Addressed context")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "original.pdf" }));
    expect(preview).toHaveBeenCalledWith(expect.objectContaining({ id: "file", name: "original.pdf" }));
    fireEvent.click(screen.getByRole("button", { name: "Other uses" }));
    expect(preview).toHaveBeenCalledTimes(1);
  });

  test("renders depth-first role parts while hiding retained transport headers", () => {
    const { container } = render(
      <MessagePartsView
        resolveFileUrl={fileUrl}
        parts={[
          { id: "prt_title", role: "TITLE", fragment: { text: "Quarterly update" }, file: null },
          { id: "prt_header", role: "HEADER", fragment: { text: "List-Id: releases.example" }, file: null },
          { id: "prt_body", role: "BODY", fragment: { text: "The build is green." }, file: null },
          { id: "prt_quote", role: "QUOTED", fragment: { text: "Earlier context" }, file: null },
          { id: "prt_sig", role: "SIGNATURE", fragment: { text: "Regards, Ada" }, file: null },
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Quarterly update" })).toBeTruthy();
    expect(screen.queryByText("List-Id: releases.example")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show quoted text" }));
    const content = container.textContent ?? "";
    expect(content.indexOf("Quarterly update")).toBeLessThan(content.indexOf("The build is green."));
    expect(content.indexOf("The build is green.")).toBeLessThan(content.indexOf("Earlier context"));
    expect(content.indexOf("Earlier context")).toBeLessThan(content.indexOf("Regards, Ada"));
    expect(content.indexOf("The build is green.")).toBeLessThan(content.indexOf("Regards, Ada"));
  });

  test("keeps quoted content collapsed until the reader opens it", () => {
    render(
      <MessagePartsView
        resolveFileUrl={fileUrl}
        parts={[{ role: "QUOTED", fragment: { text: "Earlier message" }, file: null }]}
      />,
    );

    const show = screen.getByRole("button", { name: "Show quoted text" });
    expect(show.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Earlier message")).toBeNull();

    fireEvent.click(show);

    expect(screen.getByRole("button", { name: "Hide quoted text" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Earlier message")).toBeTruthy();
  });

  test("renders only the preferred supported multipart alternative", () => {
    render(
      <MessagePartsView
        parts={[
          { id: "plain", type: "text/plain", parent: { id: "alternative", type: "multipart/alternative" }, fragment: { text: "Plain copy" } },
          { id: "html", type: "text/html", parent: { id: "alternative", type: "multipart/alternative" }, fragment: { text: "<p>Rich copy</p>" } },
          { id: "attachment", disposition: "ATTACHMENT", file: { id: "document", filename: "document.pdf" } },
        ]}
      />,
    );

    expect(screen.queryByText("Plain copy")).toBeNull();
    expect(screen.getByTitle("HTML message body")).toBeTruthy();
    expect(screen.getByText("document.pdf")).toBeTruthy();
  });

  test("splits inline CID images from attachment chips the file server opens", () => {
    render(
      <MessagePartsView
        resolveFileUrl={fileUrl}
        parts={[
          {
            role: "BODY",
            disposition: "INLINE",
            cid: "hero@cid",
            fragment: null,
            file: {
              id: "img_1",
              filename: "hero.png",
              title: "Hero",
              size_bytes: 2048,
              mime_type: { mime_type: "image/png", label: "PNG" },
            },
          },
          {
            role: "BODY",
            disposition: "ATTACHMENT",
            cid: "",
            fragment: null,
            file: {
              id: "doc_1",
              filename: "report.pdf",
              title: "",
              size_bytes: 4096,
              mime_type: { mime_type: "application/pdf", label: "PDF" },
            },
          },
        ]}
      />,
    );

    const image = screen.getByRole("img", { name: "Hero" });
    expect(image.getAttribute("src")).toBe("/files/img_1");
    expect(image.closest("a")).toBeNull();

    const attachment = screen.getByRole("link", { name: /report\.pdf/ });
    expect(attachment.getAttribute("href")).toBe("/files/doc_1");
    // The server's disposition decides between showing and downloading the file;
    // a `download` attribute would force a download of a PDF it serves inline.
    expect(attachment.hasAttribute("download")).toBe(false);
    expect(attachment.getAttribute("target")).toBe("_blank");
    expect(screen.getByText("4 KB")).toBeTruthy();
  });

  test("drops unsafe resolved file URLs", () => {
    render(
      <MessagePartsView
        resolveFileUrl={() => "javascript:alert(1)"}
        parts={[
          {
            id: "prt_unsafe",
            role: "BODY",
            disposition: "ATTACHMENT",
            cid: "",
            fragment: null,
            file: {
              id: "doc_unsafe",
              filename: "payload.txt",
              title: "",
              size_bytes: 12,
              mime_type: { mime_type: "text/plain", label: "Text" },
            },
          },
        ]}
      />,
    );

    expect(screen.queryByRole("link", { name: /payload\.txt/ })).toBeNull();
    expect(screen.getByText("payload.txt")).toBeTruthy();
  });
});

/** What Chromium 156 answers for `CSS.supports("color", value)`, measured; the unit tests' DOM admits every value. */
const CHROMIUM_PARSES_AS_COLOR: Readonly<Record<string, boolean>> = {
  "#112233": true, "#e7b008": true, "#5b5bd680": true, "oklch(0.62 0.19 264)": true,
  "'x": false, "red(": false, "[red": false, "url(x)": false, "red, blue": false, "red;position:static": false,
  "red}body{display:none": false, "#fff</style><script>alert(1)</script>": false,
  "attr(data-x type(<color>), red)": true, "\\72 ed": true, "var(--b-500)": true, "v\\61r(--b-500)": true,
  "env(safe-area-inset-top, red)": true, "red /* note": true,
  // An unclosed function parses: the browser closes it itself.
  "rgb(1 2 3": true, "oklch(0.5 0.1 200": true, "color-mix(in srgb, red, blue": true, "light-dark(red, blue": true,
  "oklch(from red l c h": true, "color-mix(in srgb, red, blue)": true,
};

describe("MessageHtmlFrame", () => {
  const srcdoc = () => screen.getByTitle("Body").getAttribute("srcdoc") ?? "";
  const framed = () => new DOMParser().parseFromString(srcdoc(), "text/html");
  /** The ring rule: the name of the layer it sits in, its selector, and its declarations in order. */
  const ring = () => {
    const [, layer = "", selector = "", body = ""] = /@layer([^{]*)\{([^{}]+)\{([^{}]*)\}\}/.exec(framed().querySelector("style")?.textContent ?? "") ?? [];
    const declarations = body.split(";").map((text) => {
      const value = text.slice(text.indexOf(":") + 1).trim();
      return { property: text.slice(0, text.indexOf(":")).trim(), value: value.replace(/\s*!important$/, ""), important: value.endsWith("!important") };
    });
    const values = (property: string) => declarations.filter((declaration) => declaration.property === property).map((declaration) => declaration.value);
    return { layer: layer.trim(), selector: selector.trim(), declarations, values };
  };
  const outerColor = () => /^2px solid (.+)$/.exec(ring().values("border")[0] ?? "")?.[1];
  const readsRootAs = (value: string) => {
    const resolved = globalThis.getComputedStyle;
    vi.spyOn(globalThis, "getComputedStyle").mockImplementation((element, pseudo) => element === document.documentElement
      ? ({ getPropertyValue: (name: string) => name === "--border-focus" ? value : "" } as CSSStyleDeclaration)
      : resolved.call(globalThis, element, pseudo));
  };
  beforeEach(() => {
    // The DOM's own `CSS` is a fresh object on every read, so the whole global stands in.
    vi.stubGlobal("CSS", {
      supports: vi.fn((property: string, value?: string) => {
        if (property !== "color" || value === undefined || !Object.hasOwn(CHROMIUM_PARSES_AS_COLOR, value)) {
          throw new Error(`no measured answer for CSS.supports(${JSON.stringify(property)}, ${JSON.stringify(value)})`);
        }
        return CHROMIUM_PARSES_AS_COLOR[value];
      }),
    });
  });
  afterEach(() => {
    document.documentElement.style.removeProperty("--border-focus");
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  test("sandboxes the body without scripts or same origin, opening links in a new tab with no referrer", () => {
    render(<MessageHtmlFrame title="Body" html={'<p><a href="https://example.test/read">Read</a></p>'} />);
    expect(screen.getByTitle("Body").getAttribute("sandbox")).toBe("allow-popups allow-popups-to-escape-sandbox");
    expect(screen.getByTitle("Body").getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(srcdoc()).toContain('<base target="_blank">');
    expect(srcdoc()).toContain('<meta name="referrer" content="no-referrer">');
    expect(srcdoc()).toContain(`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'">`);
  });

  test("makes the framed body a tab stop of its own, which a mail's own body tag keeps", () => {
    render(<MessageHtmlFrame title="Body" html={'<body tabindex="-1" style="background:#111"><p>No link</p></body>'} />);
    expect(framed().body.getAttribute("tabindex")).toBe("0");
    expect(framed().body.getAttribute("style")).toBe("background:#111");
  });

  test("names the framed document with the frame's title", () => {
    const title = `Minutes <draft> & "final" 'v2'`;
    render(<MessageHtmlFrame title={title} html="<p>Plain</p>" />);
    const framedDocument = new DOMParser().parseFromString(screen.getByTitle(title).getAttribute("srcdoc") ?? "", "text/html");
    expect(framedDocument.title).toBe(title);
    expect(framedDocument.querySelectorAll("title")).toHaveLength(1);
    expect(framedDocument.querySelector("draft")).toBeNull();
  });

  test("rings the frame's edges from the root's pseudo-element while the body or anything in the mail has keyboard focus", () => {
    render(<MessageHtmlFrame title="Body" html="<p>Plain</p>" />);
    const { layer, selector, declarations, values } = ring();
    expect(framed().querySelector("style")?.textContent).toContain("body:focus-visible{outline:none!important}");
    // The root's own pseudo-element: no mail CSS on the body or its pseudo-elements reaches it.
    expect(selector).toMatch(/^html\b/);
    expect(selector).toMatch(/::after$/);
    // Keyboard focus on the body or anything in it; never pointer focus.
    expect(selector).toContain(":has(:focus-visible)");
    expect(selector).not.toMatch(/:focus(?!-visible)/);
    // Also the root's own keyboard focus, which a mail's `<html tabindex>` gives it.
    expect(selector).toContain(":is(:focus-visible,");
    // An unnamed layer, which no mail can add rules to.
    expect(layer).toBe("");
    expect(declarations[0]).toEqual({ property: "all", value: "initial", important: true });
    expect(declarations.filter((declaration) => !declaration.important)).toEqual([]);
    expect(values("content")).toEqual(['""']);
    expect(values("position")).toEqual(["fixed"]);
    for (const side of ["top", "right", "bottom", "left"]) expect(values(side)).toEqual([expect.stringMatching(/^0(?:px)?$/)]);
    expect(values("pointer-events")).toEqual(["none"]);
    // Above the mail's own positioned content, which would otherwise cover it.
    expect(values("z-index")).toEqual(["2147483647"]);
    // Three tones: the theme's color as the border, then a black and a white line inside it, each two pixels.
    // The black or the white line holds contrast on any mail, so nothing is computed from the theme's color.
    expect(values("border")).toEqual([expect.stringMatching(/^2px solid .+$/)]);
    expect(values("border-color")).toEqual([]);
    const lines = values("box-shadow");
    expect(lines).toHaveLength(1);
    const [black, white, ...more] = (lines[0] ?? "").split(",").map((line) => line.trim());
    expect(more).toEqual([]);
    expect(black).toMatch(/^inset 0 0 0 2px (?:#000(?:000)?|black)$/i);
    expect(white).toMatch(/^inset 0 0 0 4px (?:#fff(?:fff)?|white)$/i);
    // No declaration needs a newer browser than the selector does.
    expect(declarations.map((declaration) => declaration.value).join(" ")).not.toMatch(/\bfrom\b|clamp\(|oklch\(/);
  });

  test("draws the ring in the theme's focus color the page's root resolves, asking the browser whether it is a color", () => {
    document.documentElement.style.setProperty("--border-focus", "#e7b008");
    const { unmount } = render(<MessageHtmlFrame title="Body" html="<p>Plain</p>" />);
    expect(CSS.supports).toHaveBeenCalledWith("color", "#e7b008");
    expect(outerColor()).toBe("#e7b008");
    expect(srcdoc().split("#e7b008")).toHaveLength(2);
    unmount();
    document.documentElement.style.setProperty("--border-focus", "#5b5bd680");
    render(<MessageHtmlFrame title="Body" html="<p>Plain</p>" />);
    expect(outerColor()).toBe("#5b5bd680");
  });

  test("admits a color function that is closed", () => {
    readsRootAs("color-mix(in srgb, red, blue)");
    render(<MessageHtmlFrame title="Body" html="<p>Plain</p>" />);
    expect(outerColor()).toBe("color-mix(in srgb, red, blue)");
  });

  test("an inline image's address cannot end a single-quoted attribute of the mail", () => {
    render(<MessageHtmlFrame title="Body" html="<img src='cid:logo' alt='Logo'>" inlineUrls={{ logo: "/files/o'brien.png" }} />);
    expect(srcdoc()).toContain("/files/o&#39;brien.png");
    expect(framed().querySelector("img")?.getAttribute("src")).toBe("/files/o'brien.png");
    expect(framed().querySelector("img")?.getAttribute("alt")).toBe("Logo");
  });

  test.each([
    "'x", "red(", "[red", "url(x)", "red, blue", "red;position:static", "red}body{display:none",
    "#fff</style><script>alert(1)</script>", "attr(data-x type(<color>), red)", "\\72 ed",
    "var(--b-500)", "v\\61r(--b-500)", "env(safe-area-inset-top, red)", "red /* note",
    // Parsed as colors by the browser, which closes the function; in the framed rule the open bracket would swallow the rest.
    "rgb(1 2 3", "oklch(0.5 0.1 200", "color-mix(in srgb, red, blue", "light-dark(red, blue", "oklch(from red l c h",
  ])("refuses the theme color %j for the document's own accent", (unsafe) => {
    readsRootAs(unsafe);
    render(<MessageHtmlFrame title="Body" html="<p>Plain</p>" />);
    expect(srcdoc()).not.toContain(unsafe);
    expect(outerColor()).toBe("#2457c5");
  });

  test("uses the document's own accent where the browser offers no CSS.supports", () => {
    document.documentElement.style.setProperty("--border-focus", "#e7b008");
    vi.stubGlobal("CSS", {});
    render(<MessageHtmlFrame title="Body" html="<p>Plain</p>" />);
    expect(outerColor()).toBe("#2457c5");
  });

  test("keeps its document on an ordinary render and assembles it again for a brand switch", async () => {
    const brandable = defineTheme({
      contractVersion: 1, id: "example.mail", labelKey: "mail.label", descriptionKey: "mail.description", revision: 1,
      tokens: { shared: {}, light: {}, dark: {} },
      options: {
        version: 1, defaults: { focus: "#112233" },
        parse: (value) => value as { focus: string },
        resolve: (value) => ({ shared: { "--border-focus": value.focus } }),
      },
    });
    const runtime = { themes: [{ definition: brandable }] };
    function Themed({ focus }: { focus: string }) {
      const host = useMemo(() => ({ themeId: "example.mail", colorScheme: "light" as const, options: { version: 1, value: { focus } } }), [focus]);
      return <AppRuntimeProvider runtime={runtime}><AppearanceProvider host={host}>
        <MessageHtmlFrame title="Body" html="<p>Plain</p>" />
      </AppearanceProvider></AppRuntimeProvider>;
    }
    const { rerender } = render(<Themed focus="#112233" />);
    await waitFor(() => expect(outerColor()).toBe("#112233"));
    const frame = screen.getByTitle("Body");
    const written: MutationRecord[] = [];
    const writes = new MutationObserver((records) => { written.push(...records); });
    writes.observe(frame, { attributes: true, attributeFilter: ["srcdoc"] });
    rerender(<Themed focus="#112233" />);
    await act(async () => { await Promise.resolve(); });
    written.push(...writes.takeRecords());
    expect(written).toHaveLength(0);
    rerender(<Themed focus="#e7b008" />);
    await waitFor(() => expect(outerColor()).toBe("#e7b008"));
    written.push(...writes.takeRecords());
    expect(screen.getByTitle("Body")).toBe(frame);
    expect(written.length).toBeGreaterThan(0);
    writes.disconnect();
  });

  test("sets a well-formed language and resolves cid references, admitting their origins only", () => {
    render(<MessageHtmlFrame title="Body" lang="es-MX"
      html={'<p>Hola</p><img src="cid:logo@one"><img src="cid:photo@two">'}
      inlineUrls={{ "logo@one": "data:image/png;base64,AAAA", "photo@two": "https://files.example.test/p.jpg" }} />);
    expect(srcdoc()).toContain('<html lang="es-MX">');
    expect(srcdoc()).toContain('src="data:image/png;base64,AAAA"');
    expect(srcdoc()).toContain('src="https://files.example.test/p.jpg"');
    expect(srcdoc()).toContain("img-src data: blob: https://files.example.test;");
    expect(srcdoc()).not.toContain("cid:");
  });

  test("drops an unsafe inline URL and a language that is not a language tag", () => {
    render(<MessageHtmlFrame title="Body" lang={'en" onload="x'} html={'<img src="cid:x@one">'}
      inlineUrls={{ "x@one": "javascript:alert(1)" }} />);
    expect(srcdoc()).toContain("<html><head>");
    expect(srcdoc()).toContain('src="cid:x@one"');
    expect(srcdoc()).not.toContain("javascript:");
  });

  test("blocks remote images until the reader loads them", () => {
    render(<MessageHtmlFrame title="Body" html={'<img src="https://tracker.example.test/open.gif">'} />);
    expect(srcdoc()).not.toContain("https:;");
    fireEvent.click(screen.getByRole("button", { name: "Load remote images" }));
    expect(srcdoc()).toContain("img-src data: blob: http: https:;");
    expect(screen.queryByRole("button", { name: "Load remote images" })).toBeNull();
  });

  test("offers no control when the body has no remote image, or when remote images load from the start", () => {
    const { unmount } = render(<MessageHtmlFrame title="Body" html="<p>Plain</p>" />);
    expect(screen.queryByRole("button", { name: "Load remote images" })).toBeNull();
    unmount();
    render(<MessageHtmlFrame title="Body" remoteImages html={'<img src="https://images.example.test/a.png">'} />);
    expect(screen.queryByRole("button", { name: "Load remote images" })).toBeNull();
    expect(srcdoc()).toContain("http: https:");
  });
});
