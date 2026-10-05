import { useState, type KeyboardEvent, type ReactElement } from "react";

import { tv } from "../lib/variants";
import { Chip, RemovableChip } from "../ui/chip";
import { textRoleVariants } from "../ui/text";
import { inputVariants } from "../ui/input";
import { widgetControlPresentationProps } from "../ui/widget-control";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

const tagInputVariants = tv({
  extend: inputVariants,
  base: "flex flex-wrap items-center gap-1 py-1",
  variants: {
    size: {
      sm: "h-auto min-h-btn-sm",
      md: "h-auto min-h-input-h",
      lg: "h-auto min-h-input-h-lg",
    },
  },
});

function TagInputEdit({
  value,
  onChange,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<readonly string[]>): ReactElement {
  const tags = normaliseTags(value);
  const { presentation, ...controlProps } = field?.controlProps ?? {};
  const [draft, setDraft] = useState("");

  function commit(input = draft): void {
    const next = addTags(tags, input);
    if (!sameTags(tags, next)) onChange?.(next);
    setDraft("");
  }

  function remove(index: number): void {
    onChange?.(tags.filter((_, tagIndex) => tagIndex !== index));
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit();
      return;
    }
    if (event.key === "Backspace" && draft === "" && tags.length > 0) {
      event.preventDefault();
      remove(tags.length - 1);
    }
  }

  if (readOnly) return <TagInputRead value={tags} />;

  return (
    <div
      className={tagInputVariants({
        focus: "within",
        ...widgetControlPresentationProps(presentation),
        invalid: controlProps["aria-invalid"],
      })}
    >
      {tags.map((tag, index) => (
        <RemovableChip
          key={`${tag}:${index}`}
          tone="info"
          size="sm"
          removeLabel={tag}
          onRemove={() => remove(index)}
        >
          {tag}
        </RemovableChip>
      ))}
      <input
        {...controlProps}
        ref={controlRef}
        value={draft}
        className="h-5 min-w-[7rem] flex-1 border-0 bg-transparent text-13 text-fg outline-none placeholder:text-fg-muted"
        aria-label={widgetLabel(field, "Tags")}
        placeholder={tags.length === 0 ? widgetLabel(field, "Tags") : undefined}
        onBlur={() => commit()}
        onChange={(event) => {
          const next = event.currentTarget.value;
          if (/[,\n]/.test(next)) commit(next);
          else setDraft(next);
        }}
        onKeyDown={handleKeyDown}
      />
    </div>
  );
}

function TagInputRead({
  value,
}: WidgetRenderProps<readonly string[]>): ReactElement {
  const tags = normaliseTags(value);
  if (tags.length === 0) {
    return <span className={textRoleVariants({ role: "meta" })} />;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {tags.map((tag, index) => (
        <Chip key={`${tag}:${index}`} tone="info" size="sm">
          {tag}
        </Chip>
      ))}
    </span>
  );
}

export const tagInputWidget = {
  edit: TagInputEdit,
  read: TagInputRead,
  cell: TagInputRead,
} satisfies WidgetDefinition<readonly string[]>;

function normaliseTags(value: readonly string[] | null | undefined): string[] {
  if (!value) return [];
  return value.map((tag) => tag.trim()).filter(Boolean);
}

function addTags(current: readonly string[], input: string): string[] {
  const next = new Set(current);
  for (const tag of input.split(/[,\n]/).map((item) => item.trim())) {
    if (tag) next.add(tag);
  }
  return [...next];
}

function sameTags(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((tag, index) => tag === right[index]);
}
