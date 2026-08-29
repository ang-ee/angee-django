import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type RefObject,
} from "react";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, placeholder } from "@codemirror/view";
import { basicSetup } from "codemirror";

/** Editor chrome shared by the CodeMirror-backed widgets (markdown, json). */
export const CODEMIRROR_THEME = EditorView.theme({
  "&": {
    background: "transparent",
    color: "var(--text-primary)",
    fontFamily: "var(--font-sans)",
    fontSize: "0.8125rem",
    minHeight: "12rem",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-content": {
    caretColor: "var(--brand)",
    minHeight: "12rem",
    padding: "0.5rem 0.75rem",
  },
  ".cm-line": { lineHeight: "1.5rem" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
    backgroundColor: "var(--brand-soft)",
  },
  ".cm-gutters": {
    background: "transparent",
    borderRight: "1px solid var(--border-subtle)",
    color: "var(--text-muted)",
  },
  ".cm-activeLine": { backgroundColor: "transparent" },
  ".cm-activeLineGutter": { backgroundColor: "var(--surface-inset)" },
  ".cm-placeholder": { color: "var(--text-subtle)" },
});

export interface CodeMirrorEditorOptions {
  /** The editor's text. Owned by the caller; the view syncs its document to it. */
  value: string;
  /** Called (coalesced per frame) with the new text on every edit. */
  onChange?: (value: string) => void;
  readOnly?: boolean;
  /** Placeholder shown while the document is empty. */
  placeholder: string;
  /** Language + key bindings + per-widget extensions (e.g. `markdown()`, `json()`). */
  extensions: readonly Extension[];
}

/**
 * Own a CodeMirror `EditorView`'s lifecycle for a value-controlled widget: create
 * it once into `host`, sync the document to `value`, coalesce edits to `onChange`,
 * and reconfigure read-only/placeholder in place. Returns the view ref so a caller
 * can run commands against it (e.g. a markdown toolbar). The language and any key
 * bindings are passed as `extensions`; the common chrome (basic setup, theme,
 * change listener, read-only compartments) is added here so each widget declares
 * only its own intent.
 */
export function useCodeMirrorEditor(
  host: RefObject<HTMLDivElement | null>,
  options: CodeMirrorEditorOptions,
): RefObject<EditorView | null> {
  const { value, onChange, readOnly, placeholder: placeholderText, extensions } =
    options;
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  const pendingRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const syncingRef = useRef(false);
  // Capture the create-time config so the editor is built exactly once; live
  // updates flow through the value-sync and reconfigure effects below.
  const initRef = useRef({ value, readOnly, placeholderText, extensions });
  const readOnlyCompartment = useMemo(() => new Compartment(), []);
  const editableCompartment = useMemo(() => new Compartment(), []);
  const placeholderCompartment = useMemo(() => new Compartment(), []);

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  const flushPendingChange = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (!pendingRef.current) return;
    pendingRef.current = false;
    const view = viewRef.current;
    if (view) onChangeRef.current?.(view.state.doc.toString());
  }, []);

  const scheduleChange = useCallback(
    () => {
      pendingRef.current = true;
      if (timerRef.current !== null) return;
      timerRef.current = setTimeout(flushPendingChange, 16);
    },
    [flushPendingChange],
  );

  useEffect(() => {
    const parent = host.current;
    if (!parent) return undefined;
    const init = initRef.current;
    const updateListener = EditorView.updateListener.of((update) => {
      if (!update.docChanged || syncingRef.current) return;
      scheduleChange();
    });
    const blurHandler = EditorView.domEventHandlers({ blur: flushPendingChange });
    const state = EditorState.create({
      doc: init.value,
      extensions: [
        basicSetup,
        ...init.extensions,
        CODEMIRROR_THEME,
        updateListener,
        blurHandler,
        readOnlyCompartment.of(EditorState.readOnly.of(Boolean(init.readOnly))),
        editableCompartment.of(EditorView.editable.of(!init.readOnly)),
        placeholderCompartment.of(placeholder(init.placeholderText)),
      ] satisfies Extension[],
    });
    const view = new EditorView({ parent, state });
    viewRef.current = view;
    return () => {
      flushPendingChange();
      view.destroy();
      viewRef.current = null;
    };
  }, [
    host,
    flushPendingChange,
    scheduleChange,
    readOnlyCompartment,
    editableCompartment,
    placeholderCompartment,
  ]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view || view.state.doc.toString() === value) return;
    syncingRef.current = true;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: value },
    });
    syncingRef.current = false;
  }, [value]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: [
        readOnlyCompartment.reconfigure(
          EditorState.readOnly.of(Boolean(readOnly)),
        ),
        editableCompartment.reconfigure(EditorView.editable.of(!readOnly)),
        placeholderCompartment.reconfigure(placeholder(placeholderText)),
      ],
    });
  }, [
    readOnly,
    placeholderText,
    readOnlyCompartment,
    editableCompartment,
    placeholderCompartment,
  ]);

  return viewRef;
}
