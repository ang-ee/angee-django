import { indentWithTab } from "@codemirror/commands";
import { EditorSelection } from "@codemirror/state";
import type { Command, EditorView, KeyBinding } from "@codemirror/view";

export type MarkdownCommand = Command;

type SelectionEdit = {
  text: string;
  anchor: number;
  head: number;
};

type SelectionEditor = (selected: string, fallback: string) => SelectionEdit;

function editMarkdownSelection(
  view: EditorView,
  fallback: string,
  edit: SelectionEditor,
): boolean {
  const transaction = view.state.changeByRange((range) => {
    const selected = view.state.sliceDoc(range.from, range.to);
    const next = edit(selected, fallback);
    return {
      changes: { from: range.from, to: range.to, insert: next.text },
      range: EditorSelection.range(
        range.from + next.anchor,
        range.from + next.head,
      ),
    };
  });
  view.dispatch(transaction);
  view.focus();
  return true;
}

function wrapSelection(
  before: string,
  after = before,
  fallback = "text",
): MarkdownCommand {
  return (view) =>
    editMarkdownSelection(view, fallback, (selected, defaultText) => {
      const text = selected || defaultText;
      return {
        text: `${before}${text}${after}`,
        anchor: before.length,
        head: before.length + text.length,
      };
    });
}

function prefixLines(prefix: string, fallback = "item"): MarkdownCommand {
  return (view) =>
    editMarkdownSelection(view, fallback, (selected, defaultText) => {
      const text = selected || defaultText;
      const inserted = text
        .split("\n")
        .map((line) => `${prefix}${line}`)
        .join("\n");
      return {
        text: inserted,
        anchor: prefix.length,
        head: inserted.length,
      };
    });
}

export function markdownLinkCommand(url: string): MarkdownCommand {
  return (view) =>
    editMarkdownSelection(view, "link text", (selected) => {
      const label = selected || "link text";
      return {
        text: `[${label}](${url})`,
        anchor: 1,
        head: 1 + label.length,
      };
    });
}

export const markdownBoldCommand = wrapSelection("**", "**", "bold text");
export const markdownItalicCommand = wrapSelection("_", "_", "italic text");
export const markdownInlineCodeCommand = wrapSelection("`", "`", "code");
export const markdownBulletListCommand = prefixLines("- ");
export const markdownNumberedListCommand = prefixLines("1. ");
export const markdownQuoteCommand = prefixLines("> ");

export const markdownEditorKeymap: readonly KeyBinding[] = [
  indentWithTab,
  { key: "Mod-b", preventDefault: true, run: markdownBoldCommand },
  { key: "Mod-i", preventDefault: true, run: markdownItalicCommand },
  { key: "Mod-e", preventDefault: true, run: markdownInlineCodeCommand },
  { key: "Mod-Shift-8", preventDefault: true, run: markdownBulletListCommand },
  { key: "Mod-Shift-7", preventDefault: true, run: markdownNumberedListCommand },
  { key: "Mod-Shift-.", preventDefault: true, run: markdownQuoteCommand },
];
