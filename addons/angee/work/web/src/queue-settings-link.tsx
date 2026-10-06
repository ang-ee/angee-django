import { Button, TextLink, useResourceRecordHref } from "@angee/ui";
import * as React from "react";

import { useWorkT } from "./i18n";
import { QUEUE_MODEL } from "./resources";

/**
 * A queue's operational pages stay in Work; its configuration is the queue
 * record, wherever composition places it (Settings under a suite that lifts
 * its queues there). This header link opens that record.
 */
export function QueueSettingsLink({ queueId }: { queueId: string }): React.ReactNode {
  const t = useWorkT();
  const href = useResourceRecordHref(QUEUE_MODEL)?.(queueId);
  if (!queueId || !href) return null;
  return (
    <Button asChild size="sm">
      <TextLink href={href}>{t("queue.settings")}</TextLink>
    </Button>
  );
}
