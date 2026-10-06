import { ChipList, Select, textRoleVariants, type WidgetDefinition, type WidgetRenderProps } from "@angee/ui";
import { useMemo, type ReactElement } from "react";

import { useAssignmentSubjects, type AssignmentSubjectKind } from "./assignment-subjects";
import { useIamT } from "./i18n";

export function subjectsWidget({ kinds }: { kinds: readonly AssignmentSubjectKind[] }): WidgetDefinition<readonly string[]> {
  function AssignmentSubjectsEdit({
    value,
    field,
    readOnly,
    onChange,
    onCommit,
    controlRef,
  }: WidgetRenderProps<readonly string[]>): ReactElement {
    const selected = useMemo(() => normaliseSubjects(value), [value]);
    const subjects = useAssignmentSubjects({ kinds, subjects: selected });
    const t = useIamT();
    const labels = subjects.labels;
    const available = subjects.options.filter((option) => !selected.includes(option.value));
    const label = typeof field?.label === "string" ? field.label : t("assignmentSubjects.label");

    function update(next: readonly string[]): void {
      onChange?.(next);
      onCommit?.();
    }

    if (readOnly) return <AssignmentSubjectsRead value={selected} field={field} />;

    return (
      <div className="grid gap-2">
        {selected.length > 0 ? (
          <ChipList
            items={selected.map((subject) => ({
              id: subject, label: labels.get(subject) ?? subject, text: String(labels.get(subject) ?? subject),
            }))}
            onRemove={(subject) => update(selected.filter((candidate) => candidate !== subject))}
          />
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
    const selected = useMemo(() => normaliseSubjects(value), [value]);
    const subjects = useAssignmentSubjects({ subjects: selected });
    return <ChipList items={selected.map((subject) => ({ id: subject, label: subjects.labels.get(subject) ?? subject }))} />;
  }

  return {
    edit: AssignmentSubjectsEdit,
    read: AssignmentSubjectsRead,
    cell: AssignmentSubjectsRead,
  };
}

function normaliseSubjects(value: readonly string[] | null | undefined): string[] {
  return [...new Set((value ?? []).map((subject) => subject.trim()).filter(Boolean))];
}
