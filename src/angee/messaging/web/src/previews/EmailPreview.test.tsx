// @vitest-environment happy-dom

import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import EmailPreview from "./EmailPreview";
import { EMAIL_PREVIEW_MAX_BYTES } from "./email-size";
import { SAMPLE_EMAIL, SAMPLE_TEXT_EMAIL } from "./sample-email";

const served: { body: BodyInit } = { body: "" };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(served.body)));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function preview(body: BodyInit) {
  served.body = body;
  return render(<EmailPreview file={{ url: "/files/notice.eml", name: "notice.eml", mime: "message/rfc822" }} mime="message/rfc822" />);
}

describe("EmailPreview", () => {
  test("shows the decoded subject in the message's language over the shared envelope", async () => {
    preview(SAMPLE_EMAIL);
    const subject = await screen.findByText("Las puertas abren a las ocho");
    expect(subject.getAttribute("lang")).toBe("es");
    expect(screen.getByText("Ada Example")).toBeTruthy();
    expect(screen.getByText("ada@example.test")).toBeTruthy();
    expect(screen.getByText("To Grace Example <grace@example.test>, Lin Example <lin@example.test>")).toBeTruthy();
    expect(screen.getByText("Cc Ren Example <ren@example.test>")).toBeTruthy();
  });

  test("renders the HTML in the shared sandbox with the inline image as a data URL", async () => {
    preview(SAMPLE_EMAIL);
    const frame = await screen.findByTitle("HTML email body");
    expect(frame.getAttribute("sandbox")).toBe("allow-popups allow-popups-to-escape-sandbox");
    const srcdoc = frame.getAttribute("srcdoc") ?? "";
    expect(srcdoc).toContain('<html lang="es">');
    expect(srcdoc).toContain('src="data:image/png;base64,iVBORw0KGgo="');
  });

  test("lists the attachments under the body, leaving out the inline image", async () => {
    preview(SAMPLE_EMAIL);
    const list = await screen.findByRole("list", { name: "Attachments" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(items[0]!.textContent).toContain("agenda.pdf");
    expect(items[0]!.textContent).toContain("9 B");
  });

  test("switches to the plain-text alternative in the message's language", async () => {
    preview(SAMPLE_EMAIL);
    fireEvent.click(await screen.findByRole("button", { name: "Plain text" }));
    const text = screen.getByText(/La lectura empieza a las ocho y media\./);
    expect(text.getAttribute("lang")).toBe("es");
    expect(screen.queryByTitle("HTML email body")).toBeNull();
  });

  test("offers no format toggle and no attachment list for a plain-text message", async () => {
    preview(SAMPLE_TEXT_EMAIL);
    expect(await screen.findByText("Rows A to C are held for the readers.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "HTML" })).toBeNull();
    expect(screen.queryByTitle("HTML email body")).toBeNull();
    expect(screen.queryByRole("list", { name: "Attachments" })).toBeNull();
  });

  test("refuses a file of unknown size that turns out larger than the cap", async () => {
    preview(new Uint8Array(EMAIL_PREVIEW_MAX_BYTES + 1));
    expect(await screen.findByText("This email is too large to preview.")).toBeTruthy();
  });

  test("shows the shared load-error surface when the file cannot be fetched", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
    preview("");
    expect(await screen.findByText("Could not load file")).toBeTruthy();
  });
});
