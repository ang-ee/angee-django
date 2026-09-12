import { defineBaseAddon } from "@angee/app";
import { Label, Select } from "@angee/ui";
import {
  defineThemeContribution,
  type ThemeOptionsEditorProps,
} from "@angee/ui/theme";

import { themes } from "./themes.mjs";

function PaperOptionsEditor({ value, disabled, onChange }: ThemeOptionsEditorProps) {
  const options = themes[0].options!.parse(value?.value ?? themes[0].options!.defaults);
  return (
    <Label>
      Paper tint
      <Select
        disabled={disabled}
        value={options.paperTint}
        onValueChange={(paperTint) => onChange({ version: 1, value: { paperTint } })}
        options={[
          { label: "Cream", value: "cream" },
          { label: "White", value: "white" },
        ]}
      />
    </Label>
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
