import { CALENDAR_MODEL, SOURCE_MODEL } from "@angee/calendar";
import { ConnectWithAccountAction } from "@angee/integrate";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import * as React from "react";

import { ConnectGoogleCalendar, GoogleCalendarAccounts } from "./documents";
import { useGoogleCalendarT } from "./i18n";

/** The Google Calendar connect verb: one of the user's Google accounts becomes one source of all its calendars. */
export function ConnectGoogleCalendarAction(): React.ReactElement {
  const t = useGoogleCalendarT();
  // The accounts are the user's credentials; a connected account joins them.
  const accounts = useAuthoredQuery(GoogleCalendarAccounts, undefined, { models: ["integrate.Credential"] });
  const refetchAccounts = accounts.refetch;
  const onConnected = React.useCallback(() => { void refetchAccounts(); }, [refetchAccounts]);
  // Correct as-is: calendar declares changes(CalendarSource) and changes(Calendar).
  const [connect] = useAuthoredMutation(ConnectGoogleCalendar, {
    invalidateModels: [SOURCE_MODEL, CALENDAR_MODEL],
  });

  return (
    <ConnectWithAccountAction
      facts={accounts.data?.calendar_source_backend}
      returnRoute="calendar.sources"
      onConnected={onConnected}
      label={t("connect.button")}
      title={t("connect.title")}
      description={t("connect.description")}
      errorFallback={t("connect.error")}
      connect={(credentialId) => connect({ credentialId })}
    />
  );
}
