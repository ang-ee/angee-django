import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type ReactElement,
} from "react";
import { markdown } from "@codemirror/lang-markdown";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, keymap, placeholder } from "@codemirror/view";
import { basicSetup } from "codemirror";
import {
  Bold,
  CodeXml,
  Eye,
  Italic,
  Link,
  List,
  ListOrdered,
  Quote,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Toolbar } from "../ui/toolbar";
import { widgetControlSurface } from "../ui/widget-control";
import type { WidgetDefinition, WidgetRenderProps } from "./types";
import {
  markdownBoldCommand,
  markdownBulletListCommand,
  markdownEditorKeymap,
  markdownInlineCodeCommand,
  markdownItalicCommand,
  markdownLinkCommand,
  markdownNumberedListCommand,
  markdownQuoteCommand,
  type MarkdownCommand,
} from "./markdown-codemirror";

type MarkdownMode = "source" | "preview";

const PREVIEW_PLUGINS = [remarkGfm];

const PROSE_CLASS = cn(
  "max-w-none text-13 leading-6 text-fg",
  "[&_p]:my-2 [&_h1]:my-3 [&_h1]:text-22 [&_h1]:font-semibold [&_h1]:leading-tight",
  "[&_h2]:my-2 [&_h2]:text-15 [&_h2]:font-semibold [&_h2]:leading-snug",
  "[&_h3]:my-2 [&_h3]:text-sm [&_h3]:font-semibold",
  "[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5",
  "[&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5",
  "[&_li]:my-1 [&_li>p]:my-0",
  "[&_blockquote]:my-3 [&_blockquote]:border-l-2 [&_blockquote]:border-border-subtle [&_blockquote]:pl-3 [&_blockquote]:text-fg-muted",
  "[&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-inset [&_pre]:p-3",
  "[&_code]:rounded [&_code]:bg-inset [&_code]:px-1 [&_code]:font-mono [&_code]:text-2xs",
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0",
  "[&_a]:text-link [&_a]:underline",
  "[&_table]:my-3 [&_table]:w-full [&_table]:border-collapse",
  "[&_th]:border [&_th]:border-border-subtle [&_th]:bg-sheet-2 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left",
  "[&_td]:border [&_td]:border-border-subtle [&_td]:px-2 [&_td]:py-1",
);

const CODEMIRROR_THEME = EditorView.theme({
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

function MarkdownEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  const [mode, setMode] = useState<MarkdownMode>("source");
  const [linkDraft, setLinkDraft] = useState("");
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  const pendingChangeRef = useRef<string | null>(null);
  const changeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const syncingRef = useRef(false);
  const readOnlyCompartment = useMemo(() => new Compartment(), []);
  const editableCompartment = useMemo(() => new Compartment(), []);
  const placeholderCompartment = useMemo(() => new Compartment(), []);

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  const flushPendingChange = useCallback(() => {
    if (changeTimerRef.current !== null) {
      clearTimeout(changeTimerRef.current);
      changeTimerRef.current = null;
    }
    const pending = pendingChangeRef.current;
    pendingChangeRef.current = null;
    if (pending !== null) onChangeRef.current?.(pending);
  }, []);

  const scheduleChange = useCallback(
    (next: string) => {
      pendingChangeRef.current = next;
      if (changeTimerRef.current !== null) return;
      changeTimerRef.current = setTimeout(flushPendingChange, 16);
    },
    [flushPendingChange],
  );

  useEffect(() => {
    const parent = hostRef.current;
    if (!parent) return undefined;
    const updateListener = EditorView.updateListener.of((update) => {
      if (!update.docChanged || syncingRef.current) return;
      scheduleChange(update.state.doc.toString());
    });
    const blurHandler = EditorView.domEventHandlers({
      blur: flushPendingChange,
    });
    const state = EditorState.create({
      doc: value ?? "",
      extensions: [
        basicSetup,
        markdown(),
        keymap.of(markdownEditorKeymap),
        EditorView.lineWrapping,
        CODEMIRROR_THEME,
        updateListener,
        blurHandler,
        readOnlyCompartment.of(EditorState.readOnly.of(Boolean(readOnly))),
        editableCompartment.of(EditorView.editable.of(!readOnly)),
        placeholderCompartment.of(placeholder(String(field?.label ?? "Markdown"))),
      ] satisfies Extension[],
    });
    const view = new EditorView({ parent, state });
    viewRef.current = view;
    return () => {
      flushPendingChange();
      view.destroy();
      viewRef.current = null;
    };
  }, [flushPendingChange, scheduleChange]);

  useEffect(() => {
    const view = viewRef.current;
    const next = value ?? "";
    if (!view || view.state.doc.toString() === next) return;
    syncingRef.current = true;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: next },
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
        placeholderCompartment.reconfigure(
          placeholder(String(field?.label ?? "Markdown")),
        ),
      ],
    });
  }, [editableCompartment, field?.label, placeholderCompartment, readOnly, readOnlyCompartment]);

  function runCommand(command: MarkdownCommand): void {
    const view = viewRef.current;
    if (!view || readOnly || mode === "preview") return;
    command(view);
  }

  function applyLink(): void {
    const url = linkDraft.trim();
    if (!url) return;
    runCommand(markdownLinkCommand(url));
    setLinkDraft("");
  }

  const toolbarDisabled = Boolean(readOnly) || mode === "preview";

  return (
    <div
      className={widgetControlSurface({
        focus: "within",
        readOnly,
        className: "w-full overflow-hidden",
      })}
    >
      {!readOnly ? (
        <Toolbar surface="preview" className="min-h-11 flex-wrap gap-1">
          <ToolbarButton
            label="Bold"
            icon={Bold}
            disabled={toolbarDisabled}
            onClick={() => runCommand(markdownBoldCommand)}
          />
          <ToolbarButton
            label="Italic"
            icon={Italic}
            disabled={toolbarDisabled}
            onClick={() => runCommand(markdownItalicCommand)}
          />
          <ToolbarButton
            label="Inline code"
            icon={CodeXml}
            disabled={toolbarDisabled}
            onClick={() => runCommand(markdownInlineCodeCommand)}
          />
          <Toolbar.Separator orientation="vertical" />
          <ToolbarButton
            label="Bulleted list"
            icon={List}
            disabled={toolbarDisabled}
            onClick={() => runCommand(markdownBulletListCommand)}
          />
          <ToolbarButton
            label="Numbered list"
            icon={ListOrdered}
            disabled={toolbarDisabled}
            onClick={() => runCommand(markdownNumberedListCommand)}
          />
          <ToolbarButton
            label="Quote"
            icon={Quote}
            disabled={toolbarDisabled}
            onClick={() => runCommand(markdownQuoteCommand)}
          />
          <Toolbar.Separator orientation="vertical" />
          <div className="flex min-w-36 items-center gap-1">
            <Input
              type="url"
              value={linkDraft}
              disabled={toolbarDisabled}
              aria-label="Link URL"
              placeholder="https://..."
              className="h-7"
              onChange={(event) => setLinkDraft(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  applyLink();
                }
              }}
            />
            <ToolbarButton
              label="Link"
              icon={Link}
              disabled={toolbarDisabled || linkDraft.trim() === ""}
              onClick={applyLink}
            />
          </div>
          <Toolbar.Spacer />
          <ModeButton mode="source" current={mode} onSelect={setMode} />
          <ModeButton mode="preview" current={mode} onSelect={setMode} />
        </Toolbar>
      ) : null}
      <div
        ref={hostRef}
        aria-label={String(field?.label ?? "Markdown")}
        className={mode === "preview" ? "hidden" : undefined}
      />
      {mode === "preview" ? (
        <div className="min-h-48 px-3 py-2">
          <MarkdownRead value={value} />
        </div>
      ) : null}
    </div>
  );
}

function ToolbarButton({
  label,
  icon: Icon,
  disabled,
  onClick,
}: {
  label: string;
  icon: ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  disabled?: boolean;
  onClick: () => void;
}): ReactElement {
  return (
    <Toolbar.Button
      type="button"
      buttonSize="iconSm"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      <Icon className="glyph" aria-hidden />
    </Toolbar.Button>
  );
}

function ModeButton({
  mode,
  current,
  onSelect,
}: {
  mode: MarkdownMode;
  current: MarkdownMode;
  onSelect: (mode: MarkdownMode) => void;
}): ReactElement {
  const active = mode === current;
  const Icon = mode === "source" ? CodeXml : Eye;
  const label = mode === "source" ? "Markdown source" : "Rendered preview";
  return (
    <Button
      type="button"
      variant="ghost"
      size="iconSm"
      active={active}
      aria-label={label}
      aria-pressed={active}
      title={label}
      onClick={() => onSelect(mode)}
    >
      <Icon className="glyph" aria-hidden />
    </Button>
  );
}

function MarkdownRead({
  value,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <div className={PROSE_CLASS}>
      <ReactMarkdown remarkPlugins={PREVIEW_PLUGINS}>{value ?? ""}</ReactMarkdown>
    </div>
  );
}

export const markdownEditorWidget = {
  edit: MarkdownEdit,
  read: MarkdownRead,
  cell: MarkdownRead,
} satisfies WidgetDefinition<string>;

export const markdownPreviewWidget = {
  edit: MarkdownRead,
  read: MarkdownRead,
  cell: MarkdownRead,
} satisfies WidgetDefinition<string>;
