import { type ReactElement } from "react";
import ReactMarkdown, {
  defaultUrlTransform,
  type Components,
} from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "../lib/cn";
import { useWikilinkResolver, type WikilinkResolver } from "./wikilink";

// Rendered markdown prose: the one prose class and heading scale for every
// read-only markdown surface (the widget read side, the file preview). Kept apart
// from the editor so the preview chunk loads react-markdown without CodeMirror.

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
  "[&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded-6 [&_pre]:bg-inset [&_pre]:p-3",
  "[&_code]:rounded-6 [&_code]:bg-inset [&_code]:px-1 [&_code]:font-mono [&_code]:text-2xs",
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0",
  "[&_a]:text-link [&_a]:underline",
  "[&_table]:my-3 [&_table]:w-full [&_table]:border-collapse",
  "[&_th]:border [&_th]:border-border-subtle [&_th]:bg-sheet-2 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left",
  "[&_td]:border [&_td]:border-border-subtle [&_td]:px-2 [&_td]:py-1",
);

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
            "rounded-4 font-medium underline decoration-dotted underline-offset-2 outline-none focus-visible:focus-ring",
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
 * {@link WikilinkProvider} ancestor makes `[[wikilinks]]` clickable.
 * @kind fragment
 */
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
