import { useEffect, type ReactElement } from "react";

import { cn } from "../lib/cn";
import {
  applyThemePreference,
  normaliseThemePreference,
  useThemePreference,
  type ThemePreference,
} from "../lib/theme";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

const THEME_OPTIONS = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
] satisfies readonly { value: ThemePreference; label: string }[];

function ThemePickerEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<ThemePreference>): ReactElement {
  const { preference, setPreference, system } = useThemePreference();
  const theme = normaliseThemePreference(value) ?? preference;

  useEffect(() => {
    applyThemePreference(theme);
  }, [theme, system]);

  function selectTheme(next: ThemePreference): void {
    if (readOnly) return;
    setPreference(next);
    onChange?.(next);
  }

  return (
    <span
      className="inline-flex min-w-0 flex-wrap items-center gap-1"
      role="group"
      aria-label={widgetLabel(field, "Theme")}
    >
      {THEME_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          disabled={readOnly}
          aria-pressed={theme === option.value}
          className={cn(
            "inline-flex h-8 items-center gap-2 rounded-md border border-border bg-sheet px-2 text-13 text-fg outline-none transition-colors hover:bg-inset focus-visible:focus-ring disabled:cursor-default disabled:opacity-70",
            theme === option.value &&
              "border-brand bg-brand-soft text-brand-soft-text",
          )}
          onClick={() => selectTheme(option.value)}
        >
          <span
            className={cn(
              "size-4 shrink-0 rounded-full border border-border-subtle",
              swatchClass(option.value),
            )}
            aria-hidden
          />
          <span>{option.label}</span>
        </button>
      ))}
    </span>
  );
}

function ThemePickerRead({
  value,
}: WidgetRenderProps<ThemePreference>): ReactElement {
  const theme = normaliseThemePreference(value) ?? "system";
  const label =
    THEME_OPTIONS.find((item) => item.value === theme)?.label ?? "System";
  return (
    <span className="inline-flex items-center gap-2 text-13 text-fg">
      <span
        className={cn(
          "size-4 rounded-full border border-border-subtle",
          swatchClass(theme),
        )}
        aria-hidden
      />
      {label}
    </span>
  );
}

export const themePickerWidget = {
  edit: ThemePickerEdit,
  read: ThemePickerRead,
  cell: ThemePickerRead,
} satisfies WidgetDefinition<ThemePreference>;

function swatchClass(value: ThemePreference): string {
  if (value === "dark") return "bg-fg";
  if (value === "system") {
    return "bg-[linear-gradient(135deg,var(--color-fg)_0_50%,var(--color-sheet)_50%_100%)]";
  }
  return "bg-sheet";
}
