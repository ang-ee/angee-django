import type { ReactNode } from "react";

import type { Tone } from "../../lib/tones";
import { PAGE_ELEMENT_SLOT } from "../page/types";

/** One headline statistic in a `DashboardView` — a label, a value, an optional
 * glyph and tone. Render-less like `Column`/`Field`: `DashboardView` reads these
 * props and folds the children into a single metric band, so every dashboard
 * shares one tile row. */
export interface MetricProps {
  label: ReactNode;
  value: ReactNode;
  /** Icon registry name or a node. */
  icon?: ReactNode;
  /** Semantic tone for the tile label. */
  tone?: Tone;
  /** Secondary line under the value (e.g. "3 critical"). */
  detail?: ReactNode;
}

/** Render-less marker; `DashboardView` collects these into its `MetricGrid`. */
export function Metric(_props: MetricProps): null {
  return null;
}

Object.assign(Metric, { [PAGE_ELEMENT_SLOT]: "metric" });
