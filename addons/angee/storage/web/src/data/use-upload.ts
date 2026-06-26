import { useCallback, useState } from "react";

import { errorMessage, useAuthoredMutation } from "@angee/ui";

import { useStorageT } from "../i18n";
import { StorageFileUploadBegin, StorageFileUploadFinalize } from "./documents";

const DEFAULT_MIME = "application/octet-stream";

/**
 * Lowercase SHA-256 hex of a file's bytes — the content address the begin step
 * dedups on and finalize verifies. Reads the whole file into memory, which is
 * fine for the sizes the proxy upload accepts.
 */
export async function sha256Hex(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export type UploadStatus =
  | "hashing"
  | "uploading"
  | "finalizing"
  | "done"
  | "deduped"
  | "failed";

export interface UploadTask {
  id: string;
  name: string;
  status: UploadStatus;
  error?: string;
}

export interface UploadTarget {
  driveId: string;
  folderId?: string | null;
}

const FINISHED: ReadonlySet<UploadStatus> = new Set(["done", "deduped", "failed"]);

let taskSeq = 0;

export interface StorageUpload {
  tasks: readonly UploadTask[];
  upload: (files: readonly File[], target: UploadTarget) => void;
  clearFinished: () => void;
}

/**
 * The upload protocol as a hook: per file, SHA-256 → `file_upload_begin` →
 * (proxy) `PUT` the bytes → `file_upload_finalize`, with a dedup short-circuit.
 * Each file is a `task` whose status drives the UI; `onUploaded` fires once the
 * batch settles so the caller can refetch.
 */
export function useStorageUpload(
  options: { onUploaded?: () => void } = {},
): StorageUpload {
  const { onUploaded } = options;
  const t = useStorageT();
  const [beginUpload] = useAuthoredMutation(StorageFileUploadBegin);
  const [finalizeUpload] = useAuthoredMutation(StorageFileUploadFinalize);
  const [tasks, setTasks] = useState<readonly UploadTask[]>([]);

  const patch = useCallback((id: string, next: Partial<UploadTask>) => {
    setTasks((current) =>
      current.map((task) => (task.id === id ? { ...task, ...next } : task)),
    );
  }, []);

  const runOne = useCallback(
    async (taskId: string, file: File, target: UploadTarget): Promise<void> => {
      try {
        const contentHash = await sha256Hex(file);
        const begun = await beginUpload({
          input: {
            filename: file.name,
            mime_type: file.type || DEFAULT_MIME,
            size_bytes: file.size,
            drive: target.driveId,
            folder: target.folderId ?? null,
            content_hash: contentHash,
          },
        });
        const payload = begun?.file_upload_begin;
        if (!payload || payload.error) {
          patch(taskId, {
            status: "failed",
            error: payload?.error ?? t("storage.upload.error.cannotStart"),
          });
          return;
        }
        if (payload.method === "deduped") {
          patch(taskId, { status: "deduped" });
          return;
        }
        patch(taskId, { status: "uploading" });
        const response = await fetch(payload.upload_url, {
          method: "PUT",
          body: file,
          credentials: "include",
        });
        if (!response.ok) {
          patch(taskId, {
            status: "failed",
            error: t("storage.upload.error.transfer", { status: response.status }),
          });
          return;
        }
        patch(taskId, { status: "finalizing" });
        const finalized = await finalizeUpload({
          input: {
            file: payload.file?.id ?? "",
            content_hash: contentHash,
            size_bytes: file.size,
          },
        });
        const result = finalized?.file_upload_finalize;
        if (!result || result.error) {
          patch(taskId, {
            status: "failed",
            error: result?.error ?? t("storage.upload.error.cannotFinalize"),
          });
          return;
        }
        patch(taskId, { status: "done" });
      } catch (error) {
        patch(taskId, {
          status: "failed",
          error: errorMessage(error, t("storage.upload.error.generic")),
        });
      }
    },
    [beginUpload, finalizeUpload, patch, t],
  );

  const upload = useCallback(
    (files: readonly File[], target: UploadTarget): void => {
      const started = files.map((file) => ({
        file,
        task: {
          id: `up-${(taskSeq += 1)}`,
          name: file.name,
          status: "hashing" as UploadStatus,
        },
      }));
      setTasks((current) => [...current, ...started.map((entry) => entry.task)]);
      void Promise.allSettled(
        started.map((entry) => runOne(entry.task.id, entry.file, target)),
      ).then(() => onUploaded?.());
    },
    [onUploaded, runOne],
  );

  const clearFinished = useCallback(() => {
    setTasks((current) => current.filter((task) => !FINISHED.has(task.status)));
  }, []);

  return { tasks, upload, clearFinished };
}
