import { Button, Glyph, useRecordChromeContext } from "@angee/ui";
import type { ReactElement } from "react";

import { useNotesT } from "./i18n";

/** Note-specific star chrome; IAM owns the global Share action beside it. */
export function RecordChrome(): ReactElement | null {
  const t = useNotesT();
  const record = useRecordChromeContext();
  if (record.resource !== "notes.Note") return null;
  // A header control is the one icon button, like Follow and Share beside it.
  return (
    <Button type="button" variant="ghost" size="iconSm" aria-label={t("record.star")}>
      <Glyph name="star" />
    </Button>
  );
}
