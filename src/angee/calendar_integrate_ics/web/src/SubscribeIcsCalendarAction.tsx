import { CALENDAR_MODEL, SOURCE_MODEL } from "@angee/calendar";
import { useAuthoredMutation } from "@angee/refine";
import * as React from "react";
import { ActionTrigger, MutationDialog, mutationDialogValueCodecs, type DescriptorField, type MutationDialogValues } from "@angee/ui";

import { SubscribeIcsCalendar } from "./documents";
import { useIcsT } from "./i18n";

/** The ICS subscribe verb: one dialog creates the source and its read-only calendar. */
export function SubscribeIcsCalendarAction(): React.ReactElement {
  // Correct as-is: calendar declares changes(CalendarSource) and changes(Calendar).
  // The dialog shows a refusal itself, under the field it names, so no generic toast repeats it.
  const [subscribe] = useAuthoredMutation(SubscribeIcsCalendar, {
    invalidateModels: [SOURCE_MODEL, CALENDAR_MODEL],
    errorNotification: false,
  });
  const t = useIcsT();
  const fields = React.useMemo<readonly DescriptorField[]>(
    () => [
      {
        name: "name",
        label: t("subscribe.name"),
        placeholder: t("subscribe.namePlaceholder"),
      },
      {
        name: "url",
        label: t("subscribe.url"),
        placeholder: t("subscribe.urlPlaceholder"),
        required: true,
      },
    ],
    [t],
  );

  return (
    <MutationDialog
      trigger={<ActionTrigger glyph="plus">{t("subscribe.button")}</ActionTrigger>}
      title={t("subscribe.title")}
      description={t("subscribe.description")}
      fields={fields}
      submitLabel={t("subscribe.submit")}
      submittingLabel={t("subscribe.submitting")}
      errorFallback={t("subscribe.error")}
      parseValues={parseSubscribeValues}
      onSubmit={async (values) => ({ status: "ok", data: await subscribe(values) })}
    />
  );
}

function parseSubscribeValues(values: MutationDialogValues) {
  return {
    // Blank takes the feed's own name.
    name: mutationDialogValueCodecs.string(values.name) ?? "",
    url: mutationDialogValueCodecs.requiredString(values.url, "url"),
  };
}
