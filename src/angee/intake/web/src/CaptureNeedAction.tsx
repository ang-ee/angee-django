import type { DocumentVariables } from "@angee/refine";
import {
  Button,
  Glyph,
  MutationDialog,
  actionFormSubmitResult,
  mutationDialogValueCodecs,
  useAuthoredResourceMutation,
  useEnumOptions,
  type DescriptorField,
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
  const importanceOptions = useEnumOptions(NEED_MODEL, "importance", { casing: "upper" });
  const [capture] = useAuthoredResourceMutation(CaptureNeedDocument, {
    invalidateModels: [NEED_MODEL],
    shouldInvalidate: (data) => data?.capture_need.ok === true,
  });
  const fields = React.useMemo<readonly DescriptorField[]>(
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
    <MutationDialog<CaptureNeedValues>
      trigger={<Button type="button" variant="primary" size="sm">
        <Glyph decorative name="plus" />
        {t("capture.button")}
      </Button>}
      title={t("capture.title")}
      description={t("capture.description")}
      fields={fields}
      initialValues={{ importance: "NORMAL" }}
      submitLabel={t("capture.submit")}
      submittingLabel={t("capture.submitting")}
      errorFallback={t("capture.error")}
      parseValues={(values) => parseCaptureNeedValues(values, t("capture.importanceRequired"))}
      onSubmit={async (values) =>
        actionFormSubmitResult(await capture({
          target: {
            model_label: targetModel,
            record_id: targetId,
          },
          ...values,
        }), "capture_need")
      }
    />
  );
}

function parseCaptureNeedValues(
  values: MutationDialogValues,
  importanceMessage: string,
): CaptureNeedValues {
  return {
    body: mutationDialogValueCodecs.requiredString(values.body, "body"),
    party: mutationDialogValueCodecs.string(values.party),
    importance: importanceValue(values.importance, importanceMessage),
  };
}

function importanceValue(value: unknown, message: string): CaptureNeedValues["importance"] {
  if (value === "NORMAL" || value === "IMPORTANT") return value;
  throw new TypeError(message);
}
