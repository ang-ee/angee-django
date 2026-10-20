import { Button, Glyph, useRecordChromeContext } from "@angee/ui";
import type { ReactElement } from "react";

import { useNotesT } from "./i18n";

/** Note-specific star chrome; IAM owns the global Share action beside it. */
export function RecordChrome(): ReactElement | null {
  const t = useNotesT();
  const record = useRecordChromeContext();
  if (record.resource !== "notes.Note") return null;
  return (
    <div className="flex items-center">
      <Button
        type="button"
        variant="ghost"
        size="iconMd"
        aria-label={t("record.star")}
        className="text-warning-text hover:text-warning-text"
      >
        <Glyph name="star" className="fill-current" />
      </Button>
    </div>
  );
}
