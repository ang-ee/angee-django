import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from "react";
import { json as jsonLanguage } from "@codemirror/lang-json";
import { EditorView } from "@codemirror/view";
import { JsonView, type Props as JsonViewProps } from "react-json-view-lite";

import { cn } from "../lib/cn";
import { Code } from "../ui/code";
import { useCodeMirrorEditor } from "./codemirror-editor";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

type JsonValue =
  | null
  | boolean
  | number
  | string
  | readonly JsonValue[]
  | { readonly [key: string]: JsonValue };

type JsonParseResult =
  | { ok: true; value: JsonValue }
  | { ok: false };

// The language + soft-wrap for the JSON editor; the shared hook adds the chrome.
const JSON_EXTENSIONS = [jsonLanguage(), EditorView.lineWrapping];

const EDITOR_SHELL =
  "overflow-hidden rounded-6 border border-border bg-sheet focus-within:focus-ring";

function JsonEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<JsonValue>): ReactElement {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const formatted = formatJson(value);
  // The text last reflected to/from the parent; lets an external value update
  // re-seed the draft without clobbering in-progress (possibly invalid) edits.
  const lastValue = useRef(formatted);
  const [draft, setDraft] = useState(formatted);
  const [valid, setValid] = useState(true);

  useEffect(() => {
    if (formatted === lastValue.current) return;
    lastValue.current = formatted;
    setDraft(formatted);
    setValid(true);
  }, [formatted]);

  const handleStringChange = useCallback(
    (next: string) => {
      setDraft(next);
      const parsed = parseJsonDraft(next);
      setValid(parsed.ok);
      if (!parsed.ok) return;
      lastValue.current = formatJson(parsed.value);
      onChange?.(parsed.value);
    },
    [onChange],
  );

  useCodeMirrorEditor(hostRef, {
    value: draft,
    onChange: handleStringChange,
    readOnly,
    placeholder: widgetLabel(field, "JSON"),
    extensions: JSON_EXTENSIONS,
  });

  return (
    <div>
      <div
        ref={hostRef}
        aria-label={widgetLabel(field, "JSON")}
        className={cn(EDITOR_SHELL, !readOnly && !valid && "border-danger")}
      />
      {!readOnly && !valid ? (
        <p className="mt-1 text-12 text-danger-text" role="alert">
          Invalid JSON
        </p>
      ) : null}
    </div>
  );
}

function JsonRead({ value }: WidgetRenderProps<JsonValue>): ReactElement {
  // A collapsible tree reads better than a wall of text — but only objects/arrays
  // are trees; a scalar (or empty) falls back to the compact inline form.
  if (value !== null && typeof value === "object") {
    return (
      <JsonView
        data={value as Record<string, JsonValue> | readonly JsonValue[]}
        style={JSON_VIEW_STYLES}
        shouldExpandNode={(level) => level < 2}
      />
    );
  }
  return <JsonCell value={value} />;
}

function JsonCell({ value }: WidgetRenderProps<JsonValue>): ReactElement {
  return (
    <Code box="inset" truncate className="max-w-full">
      {compactJson(value)}
    </Code>
  );
}

export const jsonWidget = {
  edit: JsonEdit,
  read: JsonRead,
  cell: JsonCell,
} satisfies WidgetDefinition<JsonValue>;

// Token-themed tree styling, so the viewer matches the app instead of importing
// the library's stylesheet. Note the library's `ariaLables` key spelling.
const JSON_VIEW_STYLES: NonNullable<JsonViewProps["style"]> = {
  container: "font-mono text-12 leading-5 text-fg",
  basicChildStyle: "ml-4",
  label: "mr-1 text-fg",
  clickableLabel: "mr-1 cursor-pointer text-fg",
  nullValue: "text-fg-subtle",
  undefinedValue: "text-fg-subtle",
  numberValue: "text-info-text",
  stringValue: "text-success-text",
  booleanValue: "text-warning-text",
  otherValue: "text-fg",
  punctuation: "text-fg-muted",
  expandIcon: "mr-1 cursor-pointer text-fg-muted after:content-['▸']",
  collapseIcon: "mr-1 cursor-pointer text-fg-muted after:content-['▾']",
  collapsedContent: "text-fg-subtle after:content-['…']",
  childFieldsContainer: "",
  ariaLables: { collapseJson: "Collapse", expandJson: "Expand" },
  stringifyStringValues: false,
};

function parseJsonDraft(input: string): JsonParseResult {
  const trimmed = input.trim();
  if (!trimmed) return { ok: true, value: null };
  try {
    return { ok: true, value: JSON.parse(trimmed) as JsonValue };
  } catch {
    return { ok: false };
  }
}

function formatJson(value: JsonValue | undefined): string {
  if (value === undefined) return "";
  return JSON.stringify(value, null, 2) ?? "";
}

function compactJson(value: JsonValue | undefined): string {
  if (value === undefined) return "";
  return JSON.stringify(value) ?? "";
}
