import type {
  Row,
} from "@angee/resources";
import {
  useCallback,
  useEffect,
  useRef,
  useState } from "react";
import {
  useUpdate,
  type BaseRecord,
  type HttpError,
  } from "@refinedev/core";

import { useDebouncedCallback } from "use-debounce";
import {
  refineFieldsFromPaths,
  } from "@angee/refine";
import {
  useAuthoredMutation,
} from "@angee/ui";
import {
  refineResourceName,
  useModelMetadata,
} from "@angee/resources";

import { KnowledgeUpdatePageBody } from "./documents";

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
const PAGE_MODEL = "knowledge.Page";

/**
 * Editing state for one page. The title persists through `updatePage` on commit;
 * the body autosaves to `updatePageBody` (debounced) carrying the last body hash
 * so a concurrent edit is rejected rather than clobbered. Mount this per page
 * (key by id) so it seeds cleanly from the loaded record.
 */
export function usePageEditor(
  pageId: string,
  initial: { title: string; body: string; bodyHash: string },
  onSaved: () => void,
): PageEditorState {
  const [title, setTitleState] = useState(initial.title);
  const [body, setBodyState] = useState(initial.body);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const bodyHashRef = useRef(initial.bodyHash);
  const savedTitleRef = useRef(initial.title);
  const mountedRef = useRef(true);
  // Held in a ref so the save callbacks stay stable across an unstable `onSaved`
  // (e.g. an inline refetch closure) — otherwise the unmount-flush effect would
  // re-run every render and spuriously save.
  const onSavedRef = useRef(onSaved);
  useEffect(() => {
    onSavedRef.current = onSaved;
  }, [onSaved]);

  const metadata = useModelMetadata(PAGE_MODEL);
  const resource = metadata?.resource ?? null;
  const updatePage = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resource ? refineResourceName(resource) : "",
    dataProviderName: resource?.schemaName,
    meta: { fields: refineFieldsFromPaths(["title"]) },
    invalidates: ["list", "many", "detail"],
  });
  const [updateBody] = useAuthoredMutation(KnowledgeUpdatePageBody);

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
        const payload = data?.update_page_body;
        if (payload?.ok && payload.markdown) {
          bodyHashRef.current = payload.markdown.body_hash;
          setSafeStatus("saved");
          onSavedRef.current();
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
      setStatus("saving");
      void debouncedSaveBody(next);
    },
    [debouncedSaveBody],
  );

  const commitTitle = useCallback(() => {
    const trimmed = title.trim();
    if (!trimmed || trimmed === savedTitleRef.current) return;
    savedTitleRef.current = trimmed;
    setStatus("saving");
    void updatePage.mutateAsync({ id: pageId, values: { title: trimmed } })
      .then(() => {
        setSafeStatus("saved");
        onSavedRef.current();
      })
      .catch(() => setSafeStatus("error"));
  }, [title, pageId, updatePage.mutateAsync, setSafeStatus]);

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
