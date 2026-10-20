import { Button, Glyph } from "@angee/ui";
import * as React from "react";

import { useMessagingT } from "./i18n";

/** The one star toggle on a message: the icon button, pressed while starred. The
 *  caller owns which mutation stars it (a message's own star, a record thread's). */
export function StarButton({ starred, disabled, onToggle }: {
  starred: boolean;
  disabled?: boolean;
  onToggle: () => void;
}): React.ReactElement {
  const t = useMessagingT();
  const label = t(starred ? "message.unstar" : "message.star");
  return (
    <Button type="button" size="iconSm" variant="ghost" aria-label={label} title={label}
      pressed={starred} disabled={disabled} onClick={onToggle}>
      <Glyph name="star" />
    </Button>
  );
}
