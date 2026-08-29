import type { DocumentVariables } from "@angee/refine";
import {
  Button,
  Glyph,
  MutationDialog,
  mutationDialogValueCodecs,
  useAuthoredResourceMutation,
  useEnumOptions,
  type MutationDialogField,
  type MutationDialogValues,
} from "@angee/ui";
import * as React from "react";

import { CaptureNeedDocument } from "./documents";
import { useIntakeT } from "./i18n";
import { NEED_MODEL, PARTY_MODEL } from "./resources";

type CaptureNeedVariables = DocumentVariables<typeof CaptureNeedDocument>;
type CaptureNeedValues = Pick<
  CaptureNeedVariables,
  "body" | "party" | "importance"
>;

export interface CaptureNeedActionProps {
  targetModel: string;
  targetId: string;
}

/** Intake-owned S7 button/dialog/mutation ceremony for manual evidence capture. */
export function CaptureNeedAction({
  targetModel,
  targetId,
}: CaptureNeedActionProps): React.ReactElement {
  const t = useIntakeT();
  const [open, setOpen] = React.useState(false);
  const declaredImportanceOptions = useEnumOptions(NEED_MODEL, "importance");
  const importanceOptions = React.useMemo(
    () =>
      declaredImportanceOptions.map((option) => ({
        ...option,
        value: String(option.value).toUpperCase(),
      })),
    [declaredImportanceOptions],
  );
  const [capture] = useAuthoredResourceMutation(CaptureNeedDocument, {
    invalidateModels: [NEED_MODEL],
    errorFrom: (data) => {
      const outcome = data?.capture_need;
      return outcome && !outcome.ok ? outcome.message : null;
    },
    shouldInvalidate: (data) => data?.capture_need.ok === true,
  });
  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "body",
        label: t("capture.body"),
        widget: "textarea",
        required: true,
      },
      {
        name: "party",
        label: t("capture.party"),
        relation: { resource: PARTY_MODEL, labelField: "display_name" },
      },
      {
        name: "importance",
        label: t("capture.importance"),
        widget: "select",
        required: true,
        options: importanceOptions,
      },
    ],
    [importanceOptions, t],
  );

  return (
    <>
      <Button type="button" variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t("capture.button")}
      </Button>
      <MutationDialog<CaptureNeedValues>
        open={open}
        onOpenChange={setOpen}
        title={t("capture.title")}
        description={t("capture.description")}
        fields={fields}
        initialValues={{ importance: "NORMAL" }}
        submitLabel={t("capture.submit")}
        submittingLabel={t("capture.submitting")}
        errorFallback={t("capture.error")}
        parseValues={parseCaptureNeedValues}
        onSubmit={(values) =>
          capture({
            target: {
              model_label: targetModel,
              record_id: targetId,
            },
            ...values,
          })
        }
      />
    </>
  );
}

function parseCaptureNeedValues(
  values: MutationDialogValues,
): CaptureNeedValues {
  return {
    body: mutationDialogValueCodecs.requiredString(values.body, "body"),
    party: mutationDialogValueCodecs.string(values.party),
    importance: importanceValue(values.importance),
  };
}

function importanceValue(value: unknown): CaptureNeedValues["importance"] {
  if (value === "NORMAL" || value === "IMPORTANT") return value;
  throw new TypeError("A need importance is required.");
}
