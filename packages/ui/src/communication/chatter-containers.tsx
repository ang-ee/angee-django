import type { ChatterViewContext, CoreContainer } from "../runtime";
import { ACTIVITY_TAB_ID, Activity } from "./Activity";

/** The framework owns one activity tab; addons contribute its model-scoped entry sources. */
export const CHATTER_CONTAINERS: readonly CoreContainer[] = [
  {
    address: "record#aside",
    models: true,
    // Pages publish their own tabs at render time; `only` may name them.
    extras: true,
    children: {
      [ACTIVITY_TAB_ID]: {
        sequence: 10,
        content: {
          label: "Activity",
          icon: "activity",
          panelClassName: "p-0",
          when: ({ view }: ChatterViewContext) => view.kind === "record" && Boolean(view.sqid),
          render: (context: ChatterViewContext) => <Activity
            key={`${context.route?.canonicalLabel ?? context.route?.modelLabel ?? ""}:${context.view.kind === "record" ? context.view.sqid ?? "" : ""}`}
            context={context}
            className="h-full"
          />,
        },
      },
    },
  },
  { address: "record#activity", models: true },
];
