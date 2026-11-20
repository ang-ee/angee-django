// @vitest-environment happy-dom

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

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

describe("MessageHtmlFrame", () => {
  const srcdoc = () => screen.getByTitle("Body").getAttribute("srcdoc") ?? "";

  test("sandboxes the body without scripts or same origin, opening links in a new tab with no referrer", () => {
    render(<MessageHtmlFrame title="Body" html={'<p><a href="https://example.test/read">Read</a></p>'} />);
    expect(screen.getByTitle("Body").getAttribute("sandbox")).toBe("allow-popups allow-popups-to-escape-sandbox");
    expect(srcdoc()).toContain('<base target="_blank">');
    expect(srcdoc()).toContain('<meta name="referrer" content="no-referrer">');
    expect(srcdoc()).toContain("default-src 'none'; img-src data: blob:;");
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
