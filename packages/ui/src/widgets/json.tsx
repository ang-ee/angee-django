import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from "react";
import { json as jsonLanguage } from "@codemirror/lang-json";
import { EditorView } from "@codemirror/view";
import { cn } from "../lib/cn";
import { useUiT } from "../i18n";
import { Code, CodeBlock } from "../ui/code";
import { useCodeMirrorEditor } from "./codemirror-editor";
import type { JsonValue } from "./json-value";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

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
  onCommit,
  field,
  readOnly,
  controlRef,
  onValidityChange,
}: WidgetRenderProps<unknown>): ReactElement {
  const t = useUiT();
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
    onValidityChange?.(true);
  }, [formatted, onValidityChange]);

  const handleStringChange = useCallback(
    (next: string) => {
      setDraft(next);
      const parsed = parseJsonDraft(next);
      setValid(parsed.ok);
      onValidityChange?.(parsed.ok);
      if (!parsed.ok) return;
      lastValue.current = formatJson(parsed.value);
      onChange?.(parsed.value);
    },
    [onChange, onValidityChange],
  );

  useCodeMirrorEditor(hostRef, {
    value: draft,
    onChange: handleStringChange,
    onBlur: onCommit,
    readOnly,
    placeholder: widgetLabel(field, t("json.label")),
    extensions: JSON_EXTENSIONS,
    controlRef,
    controlProps: field?.controlProps,
  });

  return (
    <div>
      <div
        ref={hostRef}
        className={cn(EDITOR_SHELL, !readOnly && !valid && "border-danger")}
      />
      {!readOnly && !valid ? (
        <p className="mt-1 text-12 text-danger-text" role="alert">
          {t("json.invalid")}
        </p>
      ) : null}
    </div>
  );
}

function JsonRead({ value }: WidgetRenderProps<unknown>): ReactElement {
  return (
    <CodeBlock wrap className="max-h-64 overflow-auto">
      {formatJson(value)}
    </CodeBlock>
  );
}

function JsonCell({ value }: WidgetRenderProps<unknown>): ReactElement {
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
} satisfies WidgetDefinition<unknown>;

function parseJsonDraft(input: string): JsonParseResult {
  const trimmed = input.trim();
  if (!trimmed) return { ok: true, value: null };
  try {
    return { ok: true, value: JSON.parse(trimmed) as JsonValue };
  } catch {
    return { ok: false };
  }
}

function formatJson(value: unknown): string {
  if (value === undefined) return "";
  return JSON.stringify(value, null, 2) ?? "";
}

function compactJson(value: unknown): string {
  if (value === undefined) return "";
  return JSON.stringify(value) ?? "";
}
