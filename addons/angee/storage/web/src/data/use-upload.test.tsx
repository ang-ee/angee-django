// @vitest-environment happy-dom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const uploadMocks = vi.hoisted(() => ({
  begin: vi.fn(),
  finalize: vi.fn(),
  useAuthoredMutation: vi.fn(),
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  errorMessage: (error: unknown, fallback: string) =>
    error instanceof Error ? error.message : fallback,
  useNamespaceT:
    (_namespace: string, messages: Record<string, string>) =>
    (key: string, vars?: Record<string, string | number>) => {
      let message = messages[key] ?? key;
      for (const [name, value] of Object.entries(vars ?? {})) {
        message = message.replace(`{${name}}`, String(value));
      }
      return message;
    },
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: uploadMocks.useAuthoredMutation,
}));

import { StorageFileUploadBegin, StorageFileUploadFinalize } from "./documents";
import { useStorageUpload } from "./use-upload";

describe("useStorageUpload", () => {
  beforeEach(() => {
    uploadMocks.begin.mockReset();
    uploadMocks.finalize.mockReset();
    uploadMocks.useAuthoredMutation.mockReset();
    uploadMocks.useAuthoredMutation.mockImplementation((document: unknown) => {
      if (document === StorageFileUploadBegin) return [uploadMocks.begin, {}];
      if (document === StorageFileUploadFinalize) return [uploadMocks.finalize, {}];
      throw new Error("Unexpected upload mutation document.");
    });
    uploadMocks.begin.mockResolvedValue({
      file_upload_begin: {
        method: "proxy",
        upload_url: "/upload",
        error: null,
        file: { id: "fil_draft", filename: "note.txt" },
      },
    });
    uploadMocks.finalize.mockResolvedValue({
      file_upload_finalize: {
        error: null,
        file: { id: "fil_ready", filename: "note.txt" },
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200 })));
    vi.stubGlobal("crypto", {
      subtle: {
        digest: vi.fn(async () => new Uint8Array([1, 2, 3]).buffer),
      },
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test("uploads to the configured default drive when no target is named", async () => {
    const onUploaded = vi.fn();
    const { result } = renderHook(() => useStorageUpload({ onUploaded }));

    await act(async () => {
      result.current.upload([new File(["hello"], "note.txt", { type: "text/plain" })]);
    });

    await waitFor(() => expect(onUploaded).toHaveBeenCalledOnce());
    expect(uploadMocks.begin).toHaveBeenCalledWith({
      input: {
        filename: "note.txt",
        mime_type: "text/plain",
        size_bytes: 5,
        drive: null,
        drive_slug: "",
        folder: null,
        content_hash: "010203",
      },
    });
    expect(fetch).toHaveBeenCalledWith("/upload", {
      method: "PUT",
      body: expect.any(File),
      credentials: "include",
    });
    expect(uploadMocks.finalize).toHaveBeenCalledWith({
      input: {
        file: "fil_draft",
        content_hash: "010203",
        size_bytes: 5,
      },
    });
    expect(onUploaded).toHaveBeenCalledWith([
      { id: "fil_ready", filename: "note.txt" },
    ]);
  });

  test("passes explicit drive and folder targets through the begin request", async () => {
    const onUploaded = vi.fn();
    const { result } = renderHook(() => useStorageUpload({ onUploaded }));

    await act(async () => {
      result.current.upload(
        [new File(["body"], "brief.txt", { type: "" })],
        { driveId: "drv_assets", folderId: "fld_cases" },
      );
    });

    await waitFor(() => expect(onUploaded).toHaveBeenCalledOnce());
    expect(uploadMocks.begin).toHaveBeenCalledWith({
      input: expect.objectContaining({
        filename: "brief.txt",
        mime_type: "application/octet-stream",
        drive: "drv_assets",
        drive_slug: "",
        folder: "fld_cases",
      }),
    });
  });

  test("reports deduped files without proxy transfer or finalize", async () => {
    const onUploaded = vi.fn();
    uploadMocks.begin.mockResolvedValue({
      file_upload_begin: {
        method: "deduped",
        upload_url: "",
        error: null,
        file: { id: "fil_existing", filename: "same.txt" },
      },
    });
    const { result } = renderHook(() => useStorageUpload({ onUploaded }));

    await act(async () => {
      result.current.upload([new File(["same"], "same.txt")]);
    });

    await waitFor(() => expect(onUploaded).toHaveBeenCalledOnce());
    expect(fetch).not.toHaveBeenCalled();
    expect(uploadMocks.finalize).not.toHaveBeenCalled();
    expect(onUploaded).toHaveBeenCalledWith([
      { id: "fil_existing", filename: "same.txt" },
    ]);
  });
});
