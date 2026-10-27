import {
  useRef,
  useState,
  type ReactElement,
} from "react";
import { markdown } from "@codemirror/lang-markdown";
import { EditorView, keymap } from "@codemirror/view";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Toolbar } from "../ui/toolbar";
import { widgetControlSurface, widgetControlPresentationProps } from "../ui/widget-control";
import type { WidgetDefinition, WidgetRenderProps } from "./types";
import { useCodeMirrorEditor } from "./codemirror-editor";
import { Markdown } from "./markdown-prose";
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

// Language + key bindings + soft-wrap for the markdown editor; the shared hook
// adds the common chrome (basic setup, theme, change listener, read-only).
const MARKDOWN_EXTENSIONS = [
  markdown(),
  keymap.of(markdownEditorKeymap),
  EditorView.lineWrapping,
  // Prose, not code: no line-number gutter.
  EditorView.theme({ ".cm-gutters": { display: "none" } }),
];

function MarkdownEdit({
  value,
  readOnly,
  ...props
}: WidgetRenderProps<string>): ReactElement {
  return readOnly
    ? <MarkdownRead value={value} />
    : <MarkdownEditable value={value} {...props} />;
}

function MarkdownEditable({
  value,
  onChange,
  onCommit,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<string>): ReactElement {
  const t = useUiT();
  const documentBody = field?.controlProps?.presentation === "document";
  // Saved prose opens rendered; an empty field opens ready for writing.
  const [selectedMode, setMode] = useState<MarkdownMode | null>(null);
  const mode = selectedMode ?? (value ? "preview" : "source");
  const [focused, setFocused] = useState(false);
  const [linkDraft, setLinkDraft] = useState("");
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useCodeMirrorEditor(hostRef, {
    value: value ?? "",
    onChange,
    onBlur: onCommit,
    readOnly,
    placeholder: String(field?.label ?? t("markdown.placeholder")),
    extensions: MARKDOWN_EXTENSIONS,
    controlRef,
    controlProps: field?.controlProps,
  });

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
      onFocusCapture={() => {
        setFocused(true);
        if (mode === "source") setMode("source");
      }}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false);
      }}
      className={widgetControlSurface({
        focus: "within",
        ...widgetControlPresentationProps(field?.controlProps?.presentation),
        invalid: field?.controlProps?.["aria-invalid"],
        readOnly,
        disabled: "none",
        className: "w-full overflow-hidden",
      })}
    >
      {focused && mode === "source" ? (
        <Toolbar surface="preview" className="min-h-11 flex-wrap gap-1">
          <ToolbarButton
            label={t("markdown.bold")}
            icon="bold"
            disabled={toolbarDisabled}
            onClick={() => runCommand(markdownBoldCommand)}
          />
          <ToolbarButton
            label={t("markdown.italic")}
            icon="italic"
            disabled={toolbarDisabled}
            onClick={() => runCommand(markdownItalicCommand)}
          />
          <ToolbarButton
            label={t("markdown.inlineCode")}
            icon="code-xml"
            disabled={toolbarDisabled}
            onClick={() => runCommand(markdownInlineCodeCommand)}
          />
          <Toolbar.Separator orientation="vertical" />
          <ToolbarButton
            label={t("markdown.bulletedList")}
            icon="list"
            disabled={toolbarDisabled}
            onClick={() => runCommand(markdownBulletListCommand)}
          />
          <ToolbarButton
            label={t("markdown.numberedList")}
            icon="list-ordered"
            disabled={toolbarDisabled}
            onClick={() => runCommand(markdownNumberedListCommand)}
          />
          <ToolbarButton
            label={t("markdown.quote")}
            icon="quote"
            disabled={toolbarDisabled}
            onClick={() => runCommand(markdownQuoteCommand)}
          />
          <Toolbar.Separator orientation="vertical" />
          <div className="flex min-w-36 items-center gap-1">
            <Input
              type="url"
              value={linkDraft}
              disabled={toolbarDisabled}
              aria-label={t("markdown.linkUrl")}
              placeholder={t("markdown.linkUrlPlaceholder")}
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
              label={t("markdown.link")}
              icon="link"
              disabled={toolbarDisabled || linkDraft.trim() === ""}
              onClick={applyLink}
            />
          </div>
          <Toolbar.Spacer />
          {mode === "source" ? <ModeButton mode="source" current={mode} onSelect={setMode} /> : null}
          <ModeButton mode="preview" current={mode} onSelect={setMode} />
        </Toolbar>
      ) : null}
      <div
        ref={hostRef}
        className={mode === "preview" ? "hidden" : undefined}
      />
      {mode === "preview" ? (
        <div tabIndex={0} className={`min-h-24 outline-none focus-visible:focus-ring ${documentBody ? "flow-root" : "px-3 py-2"}`}>
          <div className={documentBody ? "float-right ml-2" : "flex justify-end"}>
            <ModeButton mode="source" current={mode} onSelect={() => {
              setMode("source");
              requestAnimationFrame(() => viewRef.current?.focus());
            }} />
          </div>
          <MarkdownRead value={value} />
        </div>
      ) : null}
    </div>
  );
}

function ToolbarButton({
  label,
  icon,
  disabled,
  onClick,
}: {
  label: string;
  icon: string;
  disabled?: boolean;
  onClick: () => void;
}): ReactElement {
  return (
    <Toolbar.Button
      type="button"
      size="iconSm"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      <Glyph name={icon} />
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
  const t = useUiT();
  const active = mode === current;
  const iconName = mode === "source" ? "code-xml" : "eye";
  const label = mode === "source" ? t("markdown.source") : t("markdown.preview");
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
      <Glyph name={iconName} />
    </Button>
  );
}

function MarkdownRead({ value }: WidgetRenderProps<string>): ReactElement {
  return <Markdown value={value} />;
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
