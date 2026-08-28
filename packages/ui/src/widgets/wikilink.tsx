import {
  createContext,
  useContext,
  type ReactElement,
  type ReactNode,
} from "react";

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
 * Make `[[wikilinks]]` in any descendant `Markdown` clickable by supplying a
 * resolver. Without a provider, `[[...]]` renders as plain text. Lives apart from
 * the markdown widget so a host that only needs the provider (e.g. a knowledge
 * page wrapping its content) does not pull react-markdown/CodeMirror into its
 * chunk.
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
