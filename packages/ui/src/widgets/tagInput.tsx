import { useState, type KeyboardEvent, type ReactElement } from "react";

import { useUiT } from "../i18n";
import { tv } from "../lib/variants";
import { ChipList } from "../ui/chip";
import { inputVariants } from "../ui/input";
import { widgetControlPresentationProps } from "../ui/widget-control";
import { widgetLabel } from "./label";
import type { WidgetControlProps, WidgetDefinition, WidgetRenderProps } from "./types";

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
  const t = useUiT();
  const tags = normaliseTags(value);
  const { presentation, ...controlProps } = field?.controlProps ?? ({} as Partial<WidgetControlProps>);
  const [draft, setDraft] = useState("");

  function commit(input = draft): void {
    const next = addTags(tags, input);
    if (!sameTags(tags, next)) onChange?.(next);
    setDraft("");
  }

  function remove(tag: string): void {
    onChange?.(tags.filter((candidate) => candidate !== tag));
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit();
      return;
    }
    const last = tags.at(-1);
    if (event.key === "Backspace" && draft === "" && last !== undefined) {
      event.preventDefault();
      remove(last);
    }
  }

  if (readOnly) return <TagInputRead value={tags} />;

  return (
    <div
      className={tagInputVariants({
        ...widgetControlPresentationProps(presentation),
        focus: "within" as const,
        invalid: Boolean(controlProps["aria-invalid"]),
      })}
    >
      {/* `contents` lets the chips and the draft input share one wrapping row. */}
      <ChipList className="contents" items={tags.map((tag) => ({ id: tag, label: tag }))} onRemove={remove} />
      <input
        {...controlProps}
        ref={controlRef}
        value={draft}
        className="h-5 min-w-[7rem] flex-1 border-0 bg-transparent text-13 text-fg outline-none placeholder:text-fg-muted"
        aria-label={widgetLabel(field, t("tagInput.label"))}
        placeholder={tags.length === 0 ? widgetLabel(field, t("tagInput.label")) : undefined}
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
  return <ChipList items={normaliseTags(value).map((tag) => ({ id: tag, label: tag }))} />;
}

export const tagInputWidget = {
  edit: TagInputEdit,
  read: TagInputRead,
  cell: TagInputRead,
} satisfies WidgetDefinition<readonly string[]>;

/** Trimmed, non-empty and distinct, as `addTags` keeps them. */
function normaliseTags(value: readonly string[] | null | undefined): string[] {
  if (!value) return [];
  return [...new Set(value.map((tag) => tag.trim()).filter(Boolean))];
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
