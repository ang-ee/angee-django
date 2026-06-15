// Presentational chat primitives — styled slots for an agent/comment chat surface.
// No `@assistant-ui`/`streamdown` coupling: a consumer (the agents addon) wires the
// streaming runtime and markdown around these. Tokens + `tone`/`Fill` follow the base
// design system; copy routes through `useBaseT`.

import type { HTMLAttributes, ReactElement, ReactNode } from "react";

import { useBaseT } from "../../i18n";
import { cn } from "../../lib/cn";
import type { Tone } from "../../lib/tones";
import { Button, type ButtonProps } from "../../ui/button";
import { Tag } from "../../ui/badge";
import { CodeBlock } from "../../ui/code";
import { Kbd } from "../../ui/kbd";
import { StatusDot } from "../../ui/status-icon";
import { Textarea, textareaVariants } from "../../ui/textarea";

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

export interface ChatHeaderProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  title: ReactNode;
  subtitle?: ReactNode;
  statusLabel?: ReactNode;
  statusTone?: Tone;
  actions?: ReactNode;
}

/** Chat surface header: a status dot, title/subtitle, an optional status tag, and an
 *  actions row (cog / clear / reconnect via `ChatHeaderAction`). */
export function ChatHeader({
  title,
  subtitle,
  statusLabel,
  statusTone = "neutral",
  actions,
  className,
  ...props
}: ChatHeaderProps): ReactElement {
  return (
    <header
      className={cn("border-b border-border-subtle bg-sheet-2 px-3 py-2", className)}
      {...props}
    >
      <div className="flex min-w-0 items-center gap-2">
        <StatusDot tone={statusTone} aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="truncate text-13 font-medium text-fg">{title}</div>
          {subtitle ? <div className="truncate text-2xs text-fg-muted">{subtitle}</div> : null}
        </div>
        {statusLabel ? (
          <Tag tone={statusTone} density="compact" shape="pill">
            {statusLabel}
          </Tag>
        ) : null}
      </div>
      {actions ? (
        <div className="mt-2 flex items-center justify-end gap-1.5">{actions}</div>
      ) : null}
    </header>
  );
}

export interface ChatHeaderActionProps extends Omit<ButtonProps, "size" | "variant"> {
  size?: ButtonProps["size"];
  variant?: ButtonProps["variant"];
}

/** Compact header button (defaults to a ghost `sm`), for cog/clear/reconnect. */
export function ChatHeaderAction({
  size = "sm",
  variant = "ghost",
  className,
  ...props
}: ChatHeaderActionProps): ReactElement {
  return <Button size={size} variant={variant} className={cn("h-6 px-2 text-2xs", className)} {...props} />;
}

// ---------------------------------------------------------------------------
// Message bubble
// ---------------------------------------------------------------------------

export type ChatBubbleRole = "user" | "assistant" | "system";

export interface ChatBubbleProps extends Omit<HTMLAttributes<HTMLDivElement>, "role"> {
  role: ChatBubbleRole;
}

/** A role-aligned message bubble: user trails right on brand fill, assistant/system lead
 *  left on a bordered sheet. */
export function ChatBubble({ role, className, children, ...props }: ChatBubbleProps): ReactElement {
  return (
    <div
      className={cn("flex", role === "user" ? "justify-end" : "justify-start", className)}
      {...props}
    >
      <div
        className={cn(
          "max-w-[88%] rounded-md px-3 py-2 text-13 leading-relaxed shadow-xs",
          role === "user"
            ? "bg-brand text-on-brand"
            : "border border-border-subtle bg-sheet-2 text-fg",
        )}
      >
        {children}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------

export interface ChatComposerProps extends HTMLAttributes<HTMLDivElement> {
  input?: ReactNode;
  actions?: ReactNode;
  /** Keyboard-shortcut hint in the footer row, left of the actions (`<ChatComposerHint/>`).
   *  When set, the footer row renders even without actions. */
  hint?: ReactNode;
}

/** Composer frame: an input slot (a textarea, or an assistant-ui `ComposerPrimitive.Input`)
 *  above a footer row carrying the hint and the send/cancel actions. */
export function ChatComposer({
  input,
  actions,
  hint,
  className,
  children,
  ...props
}: ChatComposerProps): ReactElement {
  return (
    <div className={cn("space-y-2", className)} {...props}>
      {input ?? children ?? <Textarea rows={3} resize="none" />}
      {hint || actions ? (
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">{hint}</div>
          {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
        </div>
      ) : null}
    </div>
  );
}

export interface ChatComposerHintProps {
  children?: ReactNode;
  className?: string;
}

/** The keyboard-shortcut affordance under a composer; defaults to "⏎ send · ⇧⏎ newline".
 *  Pure presentation — the real keybindings live with the input the composer renders. */
export function ChatComposerHint({ children, className }: ChatComposerHintProps): ReactElement {
  const t = useBaseT();
  return (
    <span className={cn("flex items-center gap-1.5 text-2xs text-fg-muted", className)}>
      {children ?? (
        <>
          <Kbd size="sm">⏎</Kbd>
          <span>{t("chat.composer.send")}</span>
          <span aria-hidden className="opacity-60">
            ·
          </span>
          <Kbd size="sm">⇧⏎</Kbd>
          <span>{t("chat.composer.newline")}</span>
        </>
      )}
    </span>
  );
}

/** The shared textarea class for a chat composer input — lets a consumer style an
 *  assistant-ui `ComposerPrimitive.Input` like the default `<Textarea>`. */
export const chatComposerInputClassName = textareaVariants({ size: "md", resize: "none" });

// ---------------------------------------------------------------------------
// Tool call + reasoning + context
// ---------------------------------------------------------------------------

export interface ToolFallbackProps {
  toolName: string;
  /** ACP tool-call status; falls back to a running/complete/error label derived from
   *  `result`/`isError` when absent. */
  status?: string;
  input?: unknown;
  result?: unknown;
  isError?: boolean;
}

/** A collapsible tool-call card: the tool name + status, with its input and result in
 *  scrollable mono blocks. Open while running, collapsed once it has a result. */
export function ToolFallback({
  toolName,
  status,
  input,
  result,
  isError = false,
}: ToolFallbackProps): ReactElement {
  const t = useBaseT();
  const hasResult = result !== undefined && result !== null;
  const hasInput = input !== undefined && input !== null;
  const label = status ?? (!hasResult ? t("chat.tool.status.running") : isError ? t("chat.tool.status.error") : t("chat.tool.status.complete"));
  return (
    <details
      className="mb-2 rounded-md border border-border-subtle bg-inset px-2 py-1 text-2xs"
      open={!hasResult}
    >
      <summary className="cursor-pointer font-medium">
        {toolName}
        <span className="ml-1 text-fg-muted">({label})</span>
      </summary>
      {hasInput ? (
        <CodeBlock wrap tone="muted" className="mt-1 max-h-32 overflow-auto">
          {`${t("chat.tool.input")}: ${formatJson(input)}`}
        </CodeBlock>
      ) : null}
      {hasResult ? (
        <CodeBlock wrap tone={isError ? "danger" : "muted"} className="mt-1 max-h-32 overflow-auto">
          {`${t("chat.tool.result")}: ${formatJson(result)}`}
        </CodeBlock>
      ) : null}
    </details>
  );
}

export interface MessageReasoningFrameProps {
  children: ReactNode;
  className?: string;
}

/** A muted, collapsible "Thinking" frame for an assistant reasoning/thought stream. */
export function MessageReasoningFrame({ children, className }: MessageReasoningFrameProps): ReactElement {
  const t = useBaseT();
  return (
    <details className={cn("my-1 rounded-md border border-border-subtle bg-inset px-2 py-1 text-2xs", className)}>
      <summary className="cursor-pointer font-medium text-fg-muted">{t("chat.reasoning.label")}</summary>
      <CodeBlock wrap tone="muted" className="mt-1 max-h-48 overflow-auto">
        {children}
      </CodeBlock>
    </details>
  );
}

export interface ContextBlockProps {
  label: ReactNode;
  children: ReactNode;
  className?: string;
}

/** A collapsible block for a (long) system-context / metadata payload — used in the chat
 *  header's settings popover to show the rendered `<system_context>`. */
export function ContextBlock({ label, children, className }: ContextBlockProps): ReactElement {
  return (
    <details className={cn("rounded-md border border-border-subtle bg-inset px-3 py-2", className)}>
      <summary className="cursor-pointer text-2xs font-medium text-fg-muted">{label}</summary>
      <CodeBlock wrap tone="muted" className="mt-2 max-h-48 overflow-auto text-2xs">
        {children}
      </CodeBlock>
    </details>
  );
}

function formatJson(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
