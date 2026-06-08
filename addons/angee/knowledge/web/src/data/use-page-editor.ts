import { useCallback, useEffect, useRef, useState } from "react";

import { useAuthoredMutation } from "@angee/sdk";

import {
  UPDATE_PAGE_BODY_MUTATION,
  UPDATE_PAGE_MUTATION,
  type UpdatePageBodyData,
  type UpdatePageBodyVariables,
  type UpdatePageData,
  type UpdatePageVariables,
} from "./documents";

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
  onSaved: () => void,
): PageEditorState {
  const [title, setTitleState] = useState(initial.title);
  const [body, setBodyState] = useState(initial.body);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const bodyHashRef = useRef(initial.bodyHash);
  const savedTitleRef = useRef(initial.title);
  const pendingBodyRef = useRef<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  // Held in a ref so the save callbacks stay stable across an unstable `onSaved`
  // (e.g. an inline refetch closure) — otherwise the unmount-flush effect would
  // re-run every render and spuriously save.
  const onSavedRef = useRef(onSaved);
  useEffect(() => {
    onSavedRef.current = onSaved;
  }, [onSaved]);

  const [updatePage] = useAuthoredMutation<UpdatePageData, UpdatePageVariables>(
    UPDATE_PAGE_MUTATION,
  );
  const [updateBody] = useAuthoredMutation<
    UpdatePageBodyData,
    UpdatePageBodyVariables
  >(UPDATE_PAGE_BODY_MUTATION);

  const setSafeStatus = useCallback((next: SaveStatus) => {
    if (mountedRef.current) setStatus(next);
  }, []);

  const saveBody = useCallback(
    async (next: string) => {
      pendingBodyRef.current = null;
      setSafeStatus("saving");
      try {
        const data = await updateBody({
          page: pageId,
          body: next,
          expectedHash: bodyHashRef.current || null,
        });
        const payload = data?.updatePageBody;
        if (payload?.ok && payload.markdown) {
          bodyHashRef.current = payload.markdown.bodyHash;
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

  const setBody = useCallback(
    (next: string) => {
      setBodyState(next);
      pendingBodyRef.current = next;
      setStatus("saving");
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => void saveBody(next), AUTOSAVE_MS);
    },
    [saveBody],
  );

  const commitTitle = useCallback(() => {
    const trimmed = title.trim();
    if (!trimmed || trimmed === savedTitleRef.current) return;
    savedTitleRef.current = trimmed;
    setStatus("saving");
    void updatePage({ data: { id: pageId, title: trimmed } })
      .then(() => {
        setSafeStatus("saved");
        onSavedRef.current();
      })
      .catch(() => setSafeStatus("error"));
  }, [title, pageId, updatePage, setSafeStatus]);

  // Flush a pending body save when the page switches (the editor unmounts).
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
      const pending = pendingBodyRef.current;
      if (pending !== null) void saveBody(pending);
    };
  }, [saveBody]);

  return { title, body, status, setTitle: setTitleState, commitTitle, setBody };
}
