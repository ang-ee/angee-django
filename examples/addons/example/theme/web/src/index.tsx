import { defineBaseAddon } from "@angee/app";
import { FieldLabel, FieldRoot, Select, useT } from "@angee/ui";
import {
  defineThemeContribution,
  resolveThemeOptions,
  type ThemeOptionsEditorProps,
} from "@angee/ui/theme";

import { themes } from "./themes.mjs";

function PaperOptionsEditor({ definition, value, disabled, onChange }: ThemeOptionsEditorProps) {
  const t = useT("themes");
  let options;
  try {
    options = resolveThemeOptions(themes[0], value).value;
  } catch {
    options = themes[0].options!.defaults;
  }
  return (
    <FieldRoot>
      <FieldLabel>{t("paper.tint")}</FieldLabel>
      <Select
        aria-label={t("paper.tint")}
        disabled={disabled}
        value={options.paperTint}
        onValueChange={(paperTint) => onChange({ version: definition.options!.version, value: { paperTint } })}
        options={[
          { label: t("paper.tint.cream"), value: "cream" },
          { label: t("paper.tint.white"), value: "white" },
        ]}
      />
    </FieldRoot>
  );
}

export default defineBaseAddon({
  id: "example.theme",
  themes: [
    defineThemeContribution({
      definition: themes[0],
      optionsEditor: PaperOptionsEditor,
    }),
  ],
  i18n: {
    themes: {
      "paper.label": "Paper",
      "paper.description": "A consumer theme with a bundled texture and one bounded option.",
      "paper.tint": "Paper tint",
      "paper.tint.cream": "Cream",
      "paper.tint.white": "White",
    },
  },
});
