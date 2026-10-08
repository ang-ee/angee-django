import {
  useEffect,
  useMemo,
  useRef,
  useImperativeHandle,
  type RefObject,
  type Ref,
} from "react";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, placeholder } from "@codemirror/view";
import { basicSetup } from "codemirror";
import type { WidgetControlProps, WidgetFocusTarget } from "./types";

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
  // basicSetup draws its own cursor and hides the native caret, so caretColor
  // alone leaves CodeMirror's default black cursor on a dark sheet.
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--brand)" },
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
  /** Called synchronously for each edit so a following submit sees the current draft. */
  onChange?: (value: string) => void;
  onBlur?: () => void;
  readOnly?: boolean;
  /** Placeholder shown while the document is empty. */
  placeholder: string;
  /** Language + key bindings + per-widget extensions (e.g. `markdown()`, `json()`). */
  extensions: readonly Extension[];
  controlRef?: Ref<WidgetFocusTarget>;
  /** Label and error associations belong on CodeMirror's editable content. */
  controlProps?: WidgetControlProps;
}

/**
 * Own a CodeMirror `EditorView`'s lifecycle for a value-controlled widget: create
 * it once into `host`, sync the document to `value`, publish edits to `onChange`,
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
  const { value, onChange, onBlur, readOnly, placeholder: placeholderText, extensions, controlRef, controlProps } =
    options;
  const controlId = controlProps?.id;
  const describedBy = controlProps?.["aria-describedby"];
  const labelledBy = controlProps?.["aria-labelledby"];
  const invalid = controlProps?.["aria-invalid"];
  const required = controlProps?.["aria-required"];
  const contentAttributes = useMemo(() => Object.fromEntries(Object.entries({
    id: controlId, "aria-label": labelledBy ? undefined : placeholderText,
    "aria-labelledby": labelledBy, "aria-describedby": describedBy,
    "aria-invalid": invalid, "aria-required": required,
  }).filter(([, entry]) => entry !== undefined).map(([key, entry]) => [key, String(entry)])),
  [controlId, describedBy, labelledBy, invalid, required, placeholderText]);
  const viewRef = useRef<EditorView | null>(null);
  useImperativeHandle(controlRef, () => ({ focus: () => viewRef.current?.focus() }), []);
  const onChangeRef = useRef(onChange);
  const onBlurRef = useRef(onBlur);
  const syncingRef = useRef(false);
  // Capture the create-time config so the editor is built exactly once; live
  // updates flow through the value-sync and reconfigure effects below.
  const initRef = useRef({ value, readOnly, placeholderText, extensions, contentAttributes });
  const readOnlyCompartment = useMemo(() => new Compartment(), []);
  const editableCompartment = useMemo(() => new Compartment(), []);
  const placeholderCompartment = useMemo(() => new Compartment(), []);
  const attributesCompartment = useMemo(() => new Compartment(), []);

  useEffect(() => {
    onChangeRef.current = onChange;
    onBlurRef.current = onBlur;
  }, [onBlur, onChange]);

  useEffect(() => {
    const parent = host.current;
    if (!parent) return undefined;
    const init = initRef.current;
    const updateListener = EditorView.updateListener.of((update) => {
      if (!update.docChanged || syncingRef.current) return;
      onChangeRef.current?.(update.state.doc.toString());
    });
    const blurHandler = EditorView.domEventHandlers({ blur: () => { onBlurRef.current?.(); } });
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
        attributesCompartment.of(EditorView.contentAttributes.of(init.contentAttributes)),
      ] satisfies Extension[],
    });
    const view = new EditorView({ parent, state });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, [
    host,
    readOnlyCompartment,
    editableCompartment,
    placeholderCompartment,
    attributesCompartment,
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
        attributesCompartment.reconfigure(EditorView.contentAttributes.of(contentAttributes)),
      ],
    });
  }, [
    readOnly,
    placeholderText,
    readOnlyCompartment,
    editableCompartment,
    placeholderCompartment,
    attributesCompartment,
    contentAttributes,
  ]);

  return viewRef;
}
