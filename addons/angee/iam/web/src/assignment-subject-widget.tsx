import { Chip, RemovableChip, Select, textRoleVariants, type FormSpecFieldDescriptor, type WidgetDefinition, type WidgetRenderProps } from "@angee/ui";
import type { ReactElement } from "react";

import { useAssignmentSubjects } from "./assignment-subjects";
import { useIamT } from "./i18n";

function AssignmentSubjectsEdit({
  value,
  field,
  readOnly,
  onChange,
  onCommit,
  controlRef,
}: WidgetRenderProps<readonly string[]>): ReactElement {
  const item = (field as { itemTemplate?: FormSpecFieldDescriptor } | undefined)?.itemTemplate;
  const subjects = useAssignmentSubjects({ resource: item?.relation?.resource });
  const t = useIamT();
  const selected = normaliseSubjects(value);
  const labels = new Map(subjects.options.map((option) => [option.value, option.label]));
  const available = subjects.options.filter((option) => !selected.includes(option.value));
  const label = typeof field?.label === "string" ? field.label : t("assignmentSubjects.label");

  function update(next: readonly string[]): void {
    onChange?.(next);
    onCommit?.();
  }

  if (readOnly) return <AssignmentSubjectsRead value={selected} />;

  return (
    <div className="grid gap-2">
      {selected.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {selected.map((subject) => (
            <RemovableChip
              key={subject}
              tone="info"
              size="sm"
              removeLabel={String(labels.get(subject) ?? subject)}
              onRemove={() => update(selected.filter((candidate) => candidate !== subject))}
            >
              {labels.get(subject) ?? subject}
            </RemovableChip>
          ))}
        </div>
      ) : null}
      <Select
        triggerRef={controlRef}
        {...field?.controlProps}
        value=""
        options={available}
        disabled={subjects.isFetching || subjects.error != null}
        aria-label={label}
        placeholder={subjects.isFetching ? t("assignmentSubjects.loading") : t("assignmentSubjects.add")}
        onValueChange={(subject) => {
          if (!subject || selected.includes(subject)) return;
          update([...selected, subject]);
        }}
      />
      {subjects.error ? (
        <span className={textRoleVariants({ role: "caption" })}>
          {t("assignmentSubjects.unavailable")}
        </span>
      ) : null}
    </div>
  );
}

function AssignmentSubjectsRead({
  value,
}: WidgetRenderProps<readonly string[]>): ReactElement {
  const subjects = useAssignmentSubjects();
  const labels = new Map(subjects.options.map((option) => [option.value, option.label]));
  const selected = normaliseSubjects(value);
  if (selected.length === 0) return <span className={textRoleVariants({ role: "meta" })} />;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {selected.map((subject) => (
        <Chip key={subject} tone="info" size="sm">
          {labels.get(subject) ?? subject}
        </Chip>
      ))}
    </span>
  );
}

export const assignmentSubjectsWidget = {
  edit: AssignmentSubjectsEdit,
  read: AssignmentSubjectsRead,
  cell: AssignmentSubjectsRead,
} satisfies WidgetDefinition<readonly string[]>;

function normaliseSubjects(value: readonly string[] | null | undefined): string[] {
  return [...new Set((value ?? []).map((subject) => subject.trim()).filter(Boolean))];
}
