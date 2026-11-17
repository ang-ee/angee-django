import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { useActionMutation, useAuthoredQuery } from "@angee/refine";
import {
  Button,
  RelationFieldWidget,
  relationSelectedOption,
  relationValueId,
  useActionResultRun,
  type RelationFieldInfo,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";

import { WritableCalendarsDocument } from "../documents.console";
import { useCalendarT } from "../i18n";
import { CALENDAR_MODEL } from "../resources";

/** The widget an event's `calendar` field renders: the calendars its viewer may add events to. */
export const CALENDAR_PICKER_WIDGET = "angee.calendar.calendarPicker";

const CALENDAR_MODELS = [CALENDAR_MODEL];

// Calendars are made on the Calendars page; the picker only chooses among them.
const CALENDAR_RELATION: RelationFieldInfo = {
  resource: CALENDAR_MODEL,
  labelField: "name",
  canCreate: false,
  colorField: "color",
};

/** The server's `writable` filter (a refine filter): the calendars the viewer may write, and so add events to. */
const WRITABLE = [{ field: "writable", operator: "eq", value: true }] as const;

/**
 * The ids of the calendars the viewer may add events to, at most two: enough to
 * tell none (offer the viewer their own), exactly one (the form's default) and a
 * choice (no default). `undefined` while it loads.
 */
export function useWritableCalendarIds(): readonly string[] | undefined {
  const query = useAuthoredQuery(WritableCalendarsDocument, undefined, { models: CALENDAR_MODELS });
  return React.useMemo(() => query.data?.calendars.map((calendar) => calendar.id), [query.data]);
}

function fieldLabel(props: WidgetRenderProps): string | undefined {
  const label = props.field?.label;
  return typeof label === "string" ? label : props.field?.name;
}

function CalendarPickerRead(props: WidgetRenderProps): React.ReactElement {
  return <RelationFieldWidget readOnly relation={CALENDAR_RELATION} value={relationValueId(props.value) || null}
    selectedOption={relationSelectedOption(props.value, CALENDAR_RELATION.labelField, CALENDAR_RELATION.colorField)}
    aria-label={fieldLabel(props)} />;
}

/**
 * Choose the calendar an event goes on, among those the viewer writes. A viewer
 * with none is offered their own personal calendar (`ensure_personal_calendar`),
 * created only when they ask and then selected.
 */
function CalendarPickerEdit(props: WidgetRenderProps): React.ReactElement {
  const { value, onChange, onCommit, readOnly, disabled, controlRef } = props;
  const t = useCalendarT();
  const writable = useWritableCalendarIds();
  const [ensurePersonal, ensuring] = useActionMutation<ActionFieldName>("ensure_personal_calendar", {
    idArgument: null,
    invalidateModels: CALENDAR_MODELS,
  });
  const settle = useActionResultRun();
  const choose = React.useCallback((next: string) => {
    onChange?.(next);
    onCommit?.();
  }, [onChange, onCommit]);
  const startOwn = React.useCallback(async () => {
    const outcome = await settle(() => ensurePersonal(""));
    if (outcome?.ok && outcome.id) choose(outcome.id);
  }, [choose, ensurePersonal, settle]);
  const locked = Boolean(readOnly || disabled);
  return <div className="flex items-center gap-2">
    <RelationFieldWidget relation={CALENDAR_RELATION} filters={WRITABLE} value={relationValueId(value) || null}
      selectedOption={relationSelectedOption(value, CALENDAR_RELATION.labelField, CALENDAR_RELATION.colorField)}
      readOnly={locked} onChange={choose} aria-label={fieldLabel(props)} controlRef={controlRef} />
    {!locked && writable?.length === 0
      ? <Button variant="secondary" size="sm" loading={ensuring.fetching} onClick={() => void startOwn()}>
        {t("picker.startOwn")}
      </Button>
      : null}
  </div>;
}

export const calendarPickerWidget = {
  edit: CalendarPickerEdit,
  read: CalendarPickerRead,
} satisfies WidgetDefinition;
