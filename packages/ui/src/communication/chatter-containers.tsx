import type { ChatterTabContent, ChatterViewContext, CoreContainer } from "../runtime";
import { RelatedPane } from "./RelatedPane";

/** The Related tab's id in `record#aside`. */
export const RELATED_TAB_ID = "related";
/** The container the Related tab lists: one child per edge, declared by the edge's owner. */
export const RELATED_GROUPS_CONTAINER = "related#groups";

/**
 * The context aside's containers: `record#aside` children are strip tabs and
 * drill-ins contributed by addons, plus the tabs a page publishes at render
 * time; the framework declares the Related tab, whose groups are the
 * `related#groups` children the edge owners (files, pages, sources) declare.
 * The timeline addon owns the first tab.
 */
export const CHATTER_CONTAINERS: readonly CoreContainer[] = [
  {
    address: "record#aside",
    models: true,
    // Pages publish their own tabs at render time; `only` may name them.
    extras: true,
    children: {
      [RELATED_TAB_ID]: {
        sequence: 40,
        content: {
          label: "Related",
          icon: "link",
          panelClassName: "p-0",
          when: (context: ChatterViewContext) => context.view.kind === "record",
          render: (context: ChatterViewContext) => <RelatedPane context={context} />,
        } satisfies ChatterTabContent,
      },
    },
  },
  { address: RELATED_GROUPS_CONTAINER, models: true },
];
