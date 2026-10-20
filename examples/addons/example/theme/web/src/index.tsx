import { defineBaseAddon } from "@angee/app";
import { FieldLabel, FieldRoot, Select } from "@angee/ui";
import {
  defineThemeContribution,
  type ThemeOptionsEditorProps,
} from "@angee/ui/theme";

import { themes } from "./themes.mjs";

function PaperOptionsEditor({ value, disabled, onChange }: ThemeOptionsEditorProps) {
  const options = themes[0].options!.parse(value?.value ?? themes[0].options!.defaults);
  return (
    <FieldRoot>
      <FieldLabel>Paper tint</FieldLabel>
      <Select
        aria-label="Paper tint"
        disabled={disabled}
        value={options.paperTint}
        onValueChange={(paperTint) => onChange({ version: 1, value: { paperTint } })}
        options={[
          { label: "Cream", value: "cream" },
          { label: "White", value: "white" },
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
    },
  },
});
