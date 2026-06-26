import {
  createContext,
  useContext,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { markdown } from "@codemirror/lang-markdown";
import { EditorView, keymap } from "@codemirror/view";
import ReactMarkdown, {
  defaultUrlTransform,
  type Components,
} from "react-markdown";
import remarkGfm from "remark-gfm";

import { Glyph } from "../chrome/Glyph";
import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Toolbar } from "../ui/toolbar";
import { widgetControlSurface } from "../ui/widget-control";
import type { WidgetDefinition, WidgetRenderProps } from "./types";
import { useCodeMirrorEditor } from "./codemirror-editor";
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

// Language + key bindings + soft-wrap for the markdown editor; the shared hook
// adds the common chrome (basic setup, theme, change listener, read-only).
const MARKDOWN_EXTENSIONS = [
  markdown(),
  keymap.of(markdownEditorKeymap),
  EditorView.lineWrapping,
];

function MarkdownEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  const t = useBaseT();
  const [mode, setMode] = useState<MarkdownMode>("source");
  const [linkDraft, setLinkDraft] = useState("");
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useCodeMirrorEditor(hostRef, {
    value: value ?? "",
    onChange,
    readOnly,
    placeholder: String(field?.label ?? t("markdown.placeholder")),
    extensions: MARKDOWN_EXTENSIONS,
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
      className={widgetControlSurface({
        focus: "within",
        readOnly,
        className: "w-full overflow-hidden",
      })}
    >
      {!readOnly ? (
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
          <ModeButton mode="source" current={mode} onSelect={setMode} />
          <ModeButton mode="preview" current={mode} onSelect={setMode} />
        </Toolbar>
      ) : null}
      <div
        ref={hostRef}
        aria-label={String(field?.label ?? t("markdown.placeholder"))}
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
      buttonSize="iconSm"
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
  const t = useBaseT();
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

/** How a `[[wikilink]]` resolves: a way to open it, and whether it is broken. */
export interface WikilinkTarget {
  /** Open the linked page; absent when the link does not resolve. */
  onActivate?: () => void;
  /** The target does not resolve to a page (rendered as a broken link). */
  broken: boolean;
}

/** Resolve a `[[target]]` to its navigation, supplied by the host. */
export type WikilinkResolver = (target: string) => WikilinkTarget;

const WikilinkContext = createContext<WikilinkResolver | null>(null);

/**
 * Make `[[wikilinks]]` in any descendant {@link Markdown} clickable by supplying
 * a resolver. Without a provider, `[[...]]` renders as plain text.
 */
export function WikilinkProvider({
  resolve,
  children,
}: {
  resolve: WikilinkResolver;
  children: ReactNode;
}): ReactElement {
  return (
    <WikilinkContext.Provider value={resolve}>
      {children}
    </WikilinkContext.Provider>
  );
}

export function useWikilinkResolver(): WikilinkResolver | null {
  return useContext(WikilinkContext);
}

const WIKILINK_PREFIX = "wikilink:";
const WIKILINK_PATTERN = /\[\[([^\]|\n]+?)(?:\|([^\]\n]+?))?\]\]/g;

interface MdastNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdastNode[];
}

/**
 * A remark plugin that turns `[[target]]` / `[[target|display]]` in text into
 * link nodes whose href carries the target on a custom scheme (rendered by the
 * components map below). Operates on the parsed tree so code spans/blocks — where
 * `[[...]]` is a literal example — are left untouched.
 */
function remarkWikilinks() {
  return (tree: MdastNode): void => transformWikilinks(tree);
}

function transformWikilinks(node: MdastNode): void {
  if (!node.children) return;
  const next: MdastNode[] = [];
  for (const child of node.children) {
    if (child.type === "inlineCode" || child.type === "code") {
      next.push(child);
    } else if (
      child.type === "text" &&
      typeof child.value === "string" &&
      child.value.includes("[[")
    ) {
      next.push(...splitWikilinks(child.value));
    } else {
      transformWikilinks(child);
      next.push(child);
    }
  }
  node.children = next;
}

function splitWikilinks(value: string): MdastNode[] {
  const out: MdastNode[] = [];
  let last = 0;
  for (const match of value.matchAll(WIKILINK_PATTERN)) {
    const start = match.index ?? 0;
    if (start > last) out.push({ type: "text", value: value.slice(last, start) });
    const target = (match[1] ?? "").trim();
    const display = (match[2] ?? match[1] ?? "").trim();
    out.push({
      type: "link",
      url: `${WIKILINK_PREFIX}${encodeURIComponent(target)}`,
      children: [{ type: "text", value: display }],
    });
    last = start + match[0].length;
  }
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}

function wikilinkComponents(resolve: WikilinkResolver): Components {
  return {
    a({ href, children }) {
      if (typeof href !== "string" || !href.startsWith(WIKILINK_PREFIX)) {
        return <a href={href}>{children}</a>;
      }
      const { onActivate, broken } = resolve(
        decodeURIComponent(href.slice(WIKILINK_PREFIX.length)),
      );
      return (
        <button
          type="button"
          disabled={!onActivate}
          onClick={onActivate}
          className={cn(
            "rounded-sm font-medium underline decoration-dotted underline-offset-2 outline-none focus-visible:focus-ring",
            broken
              ? "text-danger-text/80"
              : "text-link hover:bg-brand-soft",
          )}
        >
          {children}
        </button>
      );
    },
  };
}

/** Render a markdown string as styled prose (GFM). The reusable read primitive
 * behind the markdown widgets and any read-only markdown surface. A
 * {@link WikilinkProvider} ancestor makes `[[wikilinks]]` clickable. */
export function Markdown({
  value,
  className,
}: {
  value: string | null | undefined;
  className?: string;
}): ReactElement {
  const resolve = useWikilinkResolver();
  return (
    <div className={cn(PROSE_CLASS, className)}>
      <ReactMarkdown
        remarkPlugins={resolve ? [...PREVIEW_PLUGINS, remarkWikilinks] : PREVIEW_PLUGINS}
        components={resolve ? wikilinkComponents(resolve) : undefined}
        urlTransform={
          resolve
            ? (url) =>
                url.startsWith(WIKILINK_PREFIX) ? url : defaultUrlTransform(url)
            : undefined
        }
      >
        {value ?? ""}
      </ReactMarkdown>
    </div>
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
