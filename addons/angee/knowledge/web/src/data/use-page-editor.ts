import type { Row } from "@angee/metadata";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  useUpdate, type BaseRecord, type HttpError, } from "@refinedev/core";

import { useDebouncedCallback } from "use-debounce";
import { refineFieldsFromPaths, useAuthoredMutation, useInvalidateAuthoredModels } from "@angee/refine";
import {
  refineResourceName,
  useModelMetadata,
} from "@angee/metadata";

import { KnowledgeUpdatePageBody, PAGE_MODEL, PAGE_READ_MODELS } from "./documents";

export type SaveStatus = "idle" | "saving" | "saved" | "error";

export interface PageEditorState {
  title: string;
  body: string;
  status: SaveStatus;
  /** Update the title locally; `commitTitle` persists it. */
  setTitle: (value: string) => void;
  /** Persist the title (on blur) when it changed. */
  commitTitle: () => void;
  /** Update the body and schedule a debounced autosave. */
  setBody: (value: string) => void;
}

const AUTOSAVE_MS = 700;

/**
 * Editing state for one page. The title persists through `updatePage` on commit;
 * the body autosaves to `updatePageBody` (debounced) carrying the last body hash
 * so a concurrent edit is rejected rather than clobbered. Mount this per page
 * (key by id) so it seeds cleanly from the loaded record.
 */
export function usePageEditor(
  pageId: string,
  initial: { title: string; body: string; bodyHash: string },
): PageEditorState {
  const [title, setTitleState] = useState(initial.title);
  const [body, setBodyState] = useState(initial.body);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const bodyHashRef = useRef(initial.bodyHash);
  const savedBodyRef = useRef(initial.body);
  const savedTitleRef = useRef(initial.title);
  const mountedRef = useRef(true);
  const pendingTitleRef = useRef<string | null>(null);
  const invalidateModels = useInvalidateAuthoredModels();

  const metadata = useModelMetadata(PAGE_MODEL);
  const resource = metadata?.resource ?? null;
  const updatePage = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resource ? refineResourceName(resource) : "",
    dataProviderName: resource?.schemaName,
    meta: { fields: refineFieldsFromPaths(["title"]) },
    invalidates: ["list", "many", "detail"],
  });
  const [updateBody] = useAuthoredMutation(KnowledgeUpdatePageBody, {
    invalidateModels: PAGE_READ_MODELS,
    shouldInvalidate: (data) => data?.update_page_body.ok === true,
  });

  const setSafeStatus = useCallback((next: SaveStatus) => {
    if (mountedRef.current) setStatus(next);
  }, []);

  const saveBody = useCallback(
    async (next: string) => {
      setSafeStatus("saving");
      try {
        const data = await updateBody({
          page: pageId,
          body: next,
          expected_hash: bodyHashRef.current || null,
        });
        if (data?.update_page_body.ok && data.update_page_body.markdown) {
          bodyHashRef.current = data.update_page_body.markdown.body_hash;
          savedBodyRef.current = next;
          setSafeStatus("saved");
        } else {
          setSafeStatus("error");
        }
      } catch {
        setSafeStatus("error");
      }
    },
    [pageId, updateBody, setSafeStatus],
  );
  const debouncedSaveBody = useDebouncedCallback(saveBody, AUTOSAVE_MS);

  const setBody = useCallback(
    (next: string) => {
      setBodyState(next);
      if (next === savedBodyRef.current) {
        debouncedSaveBody.cancel();
        setStatus("idle");
        return;
      }
      setStatus("saving");
      void debouncedSaveBody(next);
    },
    [debouncedSaveBody],
  );

  const commitTitle = useCallback(() => {
    const trimmed = title.trim();
    if (!trimmed || trimmed === savedTitleRef.current || trimmed === pendingTitleRef.current) return;
    pendingTitleRef.current = trimmed;
    setStatus("saving");
    void updatePage.mutateAsync({ id: pageId, values: { title: trimmed } })
      .then(() => {
        savedTitleRef.current = trimmed;
        setSafeStatus("saved");
        invalidateModels([PAGE_MODEL]);
      })
      .catch(() => setSafeStatus("error"))
      .finally(() => {
        if (pendingTitleRef.current === trimmed) pendingTitleRef.current = null;
      });
  }, [title, pageId, updatePage.mutateAsync, setSafeStatus, invalidateModels]);

  // Flush a pending body save when the page switches (the editor unmounts).
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      void debouncedSaveBody.flush();
    };
  }, [debouncedSaveBody]);

  return { title, body, status, setTitle: setTitleState, commitTitle, setBody };
}

type RowRecord = BaseRecord & Row;
