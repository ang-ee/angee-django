import { Button, Glyph } from "@angee/ui";
import type { ReactElement } from "react";

/**
 * Record-level chrome (star/share) the notes addon contributes into the
 * `FormView` toolbar via `FORM_VIEW_RECORD_CHROME_SLOT` — the host-provided seam
 * in action, where base ships no product affordances. These are presentational
 * (the star/share behavior is not yet wired); the example exists to demonstrate
 * the slot. The filled star uses the `warning` palette token
 * (`text-warning-text`), not a raw color, per the two-axis color rule.
 */
export function RecordChrome(): ReactElement {
  return (
    <div className="flex items-center gap-1">
      <Button
        type="button"
        variant="icon"
        size="iconMd"
        aria-label="Star"
        className="text-warning-text hover:text-warning-text"
      >
        <Glyph name="star" className="fill-current" />
      </Button>
      <Button type="button" variant="icon" size="iconMd" aria-label="Share">
        <Glyph name="share" />
      </Button>
    </div>
  );
}
