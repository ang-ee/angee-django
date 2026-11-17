// @vitest-environment happy-dom

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import EmailPreview from "./EmailPreview";
import { SAMPLE_EMAIL, SAMPLE_TEXT_EMAIL } from "./sample-email";

const served = { body: "" };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(served.body)));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function preview(body: string) {
  served.body = body;
  return render(<EmailPreview file={{ url: "/files/notice.eml", name: "notice.eml", mime: "message/rfc822" }} mime="message/rfc822" />);
}

describe("EmailPreview", () => {
  test("shows the decoded subject, sender and recipients over the sandboxed HTML body", async () => {
    preview(SAMPLE_EMAIL);
    expect(await screen.findByText("Las puertas abren a las ocho")).toBeTruthy();
    expect(screen.getByText("From Ada Example <ada@example.test>")).toBeTruthy();
    expect(screen.getByText("To Grace Example <grace@example.test>, Lin Example <lin@example.test>")).toBeTruthy();
    const frame = screen.getByTitle("HTML email body");
    expect(frame.getAttribute("sandbox")).toBe("");
    const srcdoc = frame.getAttribute("srcdoc") ?? "";
    expect(srcdoc).toContain('<html lang="es">');
    expect(srcdoc).toContain('src="data:image/png;base64,iVBORw0KGgo="');
  });

  test("switches to the plain-text alternative in the message's language", async () => {
    preview(SAMPLE_EMAIL);
    fireEvent.click(await screen.findByRole("button", { name: "Plain text" }));
    const text = screen.getByText(/La lectura empieza a las ocho y media\./);
    expect(text.getAttribute("lang")).toBe("es");
    expect(screen.queryByTitle("HTML email body")).toBeNull();
  });

  test("offers no format toggle for a plain-text message", async () => {
    preview(SAMPLE_TEXT_EMAIL);
    expect(await screen.findByText("Rows A to C are held for the readers.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "HTML" })).toBeNull();
    expect(screen.queryByTitle("HTML email body")).toBeNull();
  });

  test("shows the shared load-error surface when the file cannot be fetched", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
    preview("");
    expect(await screen.findByText("Could not load file")).toBeTruthy();
  });
});
