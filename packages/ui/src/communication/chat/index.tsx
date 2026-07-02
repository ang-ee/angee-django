// Presentational chat primitives — styled slots for an agent/comment chat surface.
// No `@assistant-ui`/`streamdown` coupling: a consumer (the agents addon) wires the
// streaming runtime and markdown around these. Tokens + `tone`/`Fill` follow the base
// design system; copy routes through `useUiT`.

import { forwardRef } from "react";
import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  ReactElement,
  ReactNode,
} from "react";

import { useUiT } from "../../i18n";
import { barVariants } from "../../layouts/bar";
import { cn } from "../../lib/cn";
import { useRender, type UseRenderRenderProp } from "../../lib/slot";
import type { Tone } from "../../lib/tones";
import { tv } from "../../lib/variants";
import { Button, type ButtonProps } from "../../ui/button";
import { Tag } from "../../ui/badge";
import { CodeBlock } from "../../ui/code";
import { POPUP_BASE, POPUP_LIST } from "../../ui/popover";
import { StatusDot } from "../../ui/status-icon";
import { textRoleVariants } from "../../ui/text";

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
          {subtitle ? <div className={textRoleVariants({ role: "caption", truncate: true })}>{subtitle}</div> : null}
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

export interface ChatBarProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  /** The leading slot — e.g. a status dot + an agent/thread chooser. */
  start?: ReactNode;
  /** The trailing slot — e.g. a single overflow (⋯) menu. */
  end?: ReactNode;
}

/** A dense, single-row chat header frame: a leading `start` slot (status + chooser) and a
 *  trailing `end` slot (overflow menu), divided by a bottom border. Pure presentation — the
 *  consumer composes the chooser/menu; this owns only the dense bar layout. */
export function ChatBar({ start, end, className, ...props }: ChatBarProps): ReactElement {
  return (
    <header
      className={cn(
        barVariants({
          edge: "bottom",
          tone: "sheet2",
          gap: 2,
          justify: "between",
        }),
        // `px-3 py-2` is the chat bar's intentional snug rhythm (off the bar pad
        // scale), so it rides on top of the recipe.
        "px-3 py-2",
        className,
      )}
      {...props}
    >
      <div className="flex min-w-0 items-center gap-2">{start}</div>
      {end ? <div className="flex shrink-0 items-center gap-1.5">{end}</div> : null}
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
// Session rail (thread/session switcher list)
// ---------------------------------------------------------------------------
// A semantic `nav > ul > li > a` list for switching between sessions/threads (the
// full-page agent sessions view backs it with its running-agents query). Distinct
// from a Menu (these are navigable destinations, not commands) and from a Select
// listbox (the active row is `aria-current="page"`, not `aria-selected`). Pure
// presentation — the consumer supplies the status slot and the router `Link` via
// `render`.

export interface SessionRailProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  /** Accessible name for the nav landmark (e.g. "Running agents"). */
  label: string;
  /** Optional header slot above the list — e.g. a "+ New" control. */
  action?: ReactNode;
  /** Marks the list busy while sessions load (skeleton rows render as children). */
  busy?: boolean;
  children: ReactNode;
}

/** The left rail of a sessions view: a labelled `nav` with an optional header action
 *  ("+ New") over a scrollable `ul` of `SessionRailItem` rows. */
export function SessionRail({
  label,
  action,
  busy,
  className,
  children,
  ...props
}: SessionRailProps): ReactElement {
  return (
    <nav
      aria-label={label}
      // Fills its host (a collapsible Workbench primary pane owns width, border,
      // and bg); keeps only the inner nav > ul scaffold.
      className={cn("flex h-full min-h-0 w-full flex-col", className)}
      {...props}
    >
      {action ? <div className="border-b border-border-subtle p-2">{action}</div> : null}
      <ul aria-busy={busy || undefined} className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-2">
        {children}
      </ul>
    </nav>
  );
}

// The dense active-row look is intentionally shared in spirit with the chrome
// `SubNavLink` (`ConsoleSubNav`), but kept separate per AGENTS.md DRY's
// "similar code, different intent": `SubNavLink` is a single-label section link
// bound to the chrome menu tree's route-active matching, whereas this is a generic
// multi-slot session row (status dot + name + muted handle) the consumer binds to
// any router `Link` via `render`. Extracting one recipe would couple two unrelated
// layers (chrome navigation ↔ the communication surface) for a few utility classes.
export const sessionRailItemVariants = tv({
  base: "flex h-8 items-center gap-2 rounded-6 px-2 text-13 text-fg-2 no-underline outline-none transition-colors hover:bg-inset hover:text-fg focus-visible:focus-ring",
  variants: {
    active: {
      true: "bg-brand-soft font-medium text-brand-soft-text hover:bg-brand-soft",
      false: "",
    },
  },
  defaultVariants: { active: false },
});

export interface SessionRailItemProps {
  /** Whether this row is the open session — sets `aria-current="page"` + `data-active`. */
  active?: boolean;
  /** Leading status slot — e.g. a `StatusDot` for the session's runtime status. */
  status?: ReactNode;
  /** Trailing muted handle slot — e.g. the model handle. */
  handle?: ReactNode;
  /** The router `Link` element to render the row as (defaults to a plain `<a>`). */
  render?: UseRenderRenderProp<{ active: boolean }>;
  className?: string;
  /** The row's primary label (the session/agent name). */
  children: ReactNode;
}

/** One session rail row: a `<li>` wrapping an anchor (the consumer's router `Link` via
 *  `render`) with a leading status slot, a truncated name, and an optional muted handle.
 *  The active row carries `aria-current="page"` and a `data-active` highlight. */
export function SessionRailItem({
  active = false,
  status,
  handle,
  render,
  className,
  children,
}: SessionRailItemProps): ReactElement {
  const anchor = useRender<{ active: boolean }, HTMLElement>({
    defaultTagName: "a",
    render,
    state: { active },
    props: {
      "aria-current": active ? "page" : undefined,
      "data-active": active || undefined,
      className: sessionRailItemVariants({ active, className }),
      children: (
        <>
          {status}
          <span className="min-w-0 flex-1 truncate">{children}</span>
          {handle ? (
            <span className={cn(textRoleVariants({ role: "caption", truncate: true }), "shrink-0")}>{handle}</span>
          ) : null}
        </>
      ),
    },
  });
  return <li>{anchor}</li>;
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
          "max-w-[88%] rounded-6 px-3 py-2 text-13 leading-relaxed shadow-xs",
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

export interface ChatTypingIndicatorProps {
  /** Overrides the announced label (defaults to `chat.typing`). */
  label?: ReactNode;
  className?: string;
}

/** An animated three-dot "thinking" indicator for a started-but-empty assistant turn.
 *  Presentation only — the consumer gates it on the runtime's running state (e.g. the
 *  assistant-ui `ThreadPrimitive.If running` filter on the last empty message). The dots are
 *  `aria-hidden`; an `aria-live="polite"` label (default `chat.typing`) announces it to
 *  assistive tech, and the motion is gated behind `motion-safe`. */
export function ChatTypingIndicator({ label, className }: ChatTypingIndicatorProps): ReactElement {
  const t = useUiT();
  return (
    <div aria-live="polite" className={cn("flex items-center gap-1.5", className)}>
      <span className="sr-only">{label ?? t("chat.typing")}</span>
      <span aria-hidden className="flex items-center gap-1">
        <span className="size-1.5 rounded-full bg-fg-muted motion-safe:animate-bounce [animation-delay:-0.3s]" />
        <span className="size-1.5 rounded-full bg-fg-muted motion-safe:animate-bounce [animation-delay:-0.15s]" />
        <span className="size-1.5 rounded-full bg-fg-muted motion-safe:animate-bounce" />
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Slash-command palette (presentational slots)
// ---------------------------------------------------------------------------
// Styled, assistant-ui-free slots a consumer composes into a trigger/slash popover.
// The popover binding (e.g. assistant-ui's `Unstable_TriggerPopover`) supplies the
// listbox `role`/`id`/`aria-*` and the per-item `role`/`data-highlighted` via a Slot
// merge, so each slot must SPREAD the merged props + ref and never hard-set those.

export type ChatCommandListProps = HTMLAttributes<HTMLDivElement>;

/** The floating command-palette panel: a popover-surfaced, scrollable list. The binding
 *  merges `role="listbox"`/`id`/`aria-*` onto it, so it only spreads props + ref. */
export const ChatCommandList = forwardRef<HTMLDivElement, ChatCommandListProps>(
  function ChatCommandList({ className, ...props }, ref) {
    return <div ref={ref} className={cn(POPUP_BASE, POPUP_LIST, className)} {...props} />;
  },
);

export interface ChatCommandItemProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  label: ReactNode;
  description?: ReactNode;
}

/** One command row: a left-aligned label over an optional muted description. The binding
 *  merges `role="option"`/`data-highlighted`/handlers onto the button, so it spreads the
 *  rest + ref and only owns the visual. */
export const ChatCommandItem = forwardRef<HTMLButtonElement, ChatCommandItemProps>(
  function ChatCommandItem({ label, description, className, ...props }, ref) {
    return (
      <button
        ref={ref}
        type="button"
        className={cn(
          "flex w-full flex-col items-start gap-0.5 rounded-6 px-2 py-1.5 text-left outline-none data-[highlighted]:bg-inset",
          className,
        )}
        {...props}
      >
        <span className="text-13 text-fg">{label}</span>
        {description ? <span className={textRoleVariants({ role: "caption", truncate: true })}>{description}</span> : null}
      </button>
    );
  },
);

export interface ChatCommandEmptyProps {
  children: ReactNode;
  className?: string;
}

/** The "no matching commands" row shown when the query filters every command out. */
export function ChatCommandEmpty({ children, className }: ChatCommandEmptyProps): ReactElement {
  return <div className={cn(textRoleVariants({ role: "caption" }), "px-2 py-1.5", className)}>{children}</div>;
}

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
  const t = useUiT();
  const hasResult = result !== undefined && result !== null;
  const hasInput = input !== undefined && input !== null;
  const label = status ?? (!hasResult ? t("chat.tool.status.running") : isError ? t("chat.tool.status.error") : t("chat.tool.status.complete"));
  return (
    <details
      className="mb-2 rounded-6 border border-border-subtle bg-inset px-2 py-1 text-2xs"
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
  const t = useUiT();
  return (
    <details className={cn("my-1 rounded-6 border border-border-subtle bg-inset px-2 py-1 text-2xs", className)}>
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
    <details className={cn("rounded-6 border border-border-subtle bg-inset px-3 py-2", className)}>
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
