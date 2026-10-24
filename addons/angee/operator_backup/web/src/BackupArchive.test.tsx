// @vitest-environment happy-dom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { BackupArchiveView } from "./BackupArchive";

afterEach(cleanup);
const file = { id: "fil_archive", filename: "capture.zip", url: "/authorized/files/capture.zip", size_bytes: "4096", content_hash: "a".repeat(64), is_trashed: false };

test("download uses the File owner's authorized URL and filename", () => {
  render(<BackupArchiveView file={file} pending={false} />);
  const link = screen.getByRole("link", { name: "Download" });
  expect(link.getAttribute("href")).toBe(file.url);
  expect(link.getAttribute("download")).toBe(file.filename);
  expect(screen.getByText(/4 KB/)).toBeTruthy();
  expect(screen.getByText(new RegExp(file.content_hash))).toBeTruthy();
});

test("an unavailable or pending archive exposes no download link", () => {
  const view = render(<BackupArchiveView pending />);
  expect(screen.queryByRole("link", { name: "Download" })).toBeNull();
  expect(screen.getByRole("status").textContent).toMatch(/Loading archive/);
  expect(screen.queryByText(/Archive is unavailable/)).toBeNull();
  expect(screen.getByRole("button", { name: "Download" }).hasAttribute("disabled")).toBe(true);
  view.rerender(<BackupArchiveView file={{ ...file, url: "" }} pending={false} />);
  expect(screen.queryByRole("link", { name: "Download" })).toBeNull();
  view.rerender(<BackupArchiveView file={{ ...file, is_trashed: true }} pending={false} />);
  expect(screen.queryByRole("link", { name: "Download" })).toBeNull();
});

test("a completed backup whose File is inaccessible reports unavailable rather than pending", () => {
  render(<BackupArchiveView pending={false} />);
  expect(screen.getByText(/Archive is unavailable or you do not have access/)).toBeTruthy();
  expect(screen.queryByText("Loading archive…")).toBeNull();
});
