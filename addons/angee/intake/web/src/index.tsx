import { defineBaseAddon } from "@angee/app";
import { PROJECT_MODEL, TASK_MODEL } from "@angee/projects";
import {
  Glyph,
  Tab,
  formViewSectionsSlot,
  useRecordChromeContext,
} from "@angee/ui";
import { MessageSquareQuote } from "lucide-react";
import type { ReactElement } from "react";

import { enIntakeMessages, useIntakeT } from "./i18n";
import { RecordNeedsPane } from "./RecordNeedsPane";

export { NEED_MODEL } from "./resources";

const intake = defineBaseAddon({
  id: "intake",
  i18n: { intake: enIntakeMessages },
  icons: { "intake-needs": MessageSquareQuote },
  slots: [
    {
      ...formViewSectionsSlot(PROJECT_MODEL),
      id: "intake.project-needs",
      sequence: 30,
      content: (
        <Tab
          id="needs"
          label={<NeedsLabel />}
          icon={<Glyph decorative name="intake-needs" />}
        >
          <RecordNeedsSection targetField="project" />
        </Tab>
      ),
    },
    {
      ...formViewSectionsSlot(TASK_MODEL),
      id: "intake.task-needs",
      sequence: 30,
      content: (
        <Tab
          id="needs"
          label={<NeedsLabel />}
          icon={<Glyph decorative name="intake-needs" />}
        >
          <RecordNeedsSection targetField="task" />
        </Tab>
      ),
    },
  ],
});

function RecordNeedsSection({
  targetField,
}: {
  targetField: "task" | "project";
}): ReactElement {
  const context = useRecordChromeContext();
  return (
    <RecordNeedsPane
      targetModel={context.resource}
      targetField={targetField}
      targetId={context.recordId}
    />
  );
}

function NeedsLabel(): ReactElement {
  const t = useIntakeT();
  return <>{t("needs.label")}</>;
}

export default intake;
