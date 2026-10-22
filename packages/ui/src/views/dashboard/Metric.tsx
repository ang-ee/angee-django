import type { MetricTileValue } from "../../fragments/MetricStrip";
import { PAGE_ELEMENT_SLOT } from "../page/types";

/** One headline statistic in a `DashboardView` — a label, a value, an optional
 * glyph and tone. Render-less like `Column`/`Field`: `DashboardView` reads these
 * props and folds the children into a single metric band, so every dashboard
 * shares one tile row. */
export interface MetricProps extends Pick<MetricTileValue,
  "label" | "value" | "icon" | "tone" | "detail" | "href" | "selected" | "onClick" | "disabled"
> {
  /** Format numeric and absent values as a count; pending absent counts show an em dash. */
  format?: "count";
  loading?: boolean;
  /** Render counts above this bound as `<bound>+`. */
  max?: number;
}

/** Render-less marker; `DashboardView` collects these into one prominent metric strip.
 * @kind contribution
 */
export function Metric(_props: MetricProps): null {
  return null;
}

Object.assign(Metric, { [PAGE_ELEMENT_SLOT]: "metric" });
