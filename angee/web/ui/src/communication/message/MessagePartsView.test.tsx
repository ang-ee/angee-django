// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { MessagePartsView, type MessagePartFile } from "./index";

function fileUrl(file: MessagePartFile): string {
  return `/files/${file.id}`;
}

describe("MessagePartsView", () => {
  afterEach(() => {
    cleanup();
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

  test("splits inline CID images from downloadable attachment chips", () => {
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
