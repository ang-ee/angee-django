import {
  useAuthoredMutation,
  type TypedDocumentNode,
} from "@angee/refine";
import {
  Button,
  Glyph,
  MutationDialog,
  mutationDialogValueCodecs,
  type MutationDialogControlProps,
  type MutationDialogField,
  type MutationDialogValues,
} from "@angee/ui";
import * as React from "react";

import { useStorageIntegrateT } from "./i18n";
import { MountSourceBrowser } from "./MountSourceBrowser";

export type MountConnectMode = "COPY" | "REFERENCE";

export type MountConnectVariables = {
  name: string;
  path: string;
  mode: MountConnectMode;
};

export interface ConnectMountActionProps {
  mutationDocument: TypedDocumentNode<unknown, MountConnectVariables>;
  backendClass: string;
  i18nPrefix: string;
  invalidateModel: string;
}

/**
 * Canonical economy for browsable Mount connection: this factory owns the
 * button, typed dialog, browser field, generic labels, busy copy, mutation, and
 * invalidation. Backends supply only their operation/class and genuinely varying
 * button/title/description/placeholder/error range.
 *
 * Backend message ranges arrive through addon composition. Providerless raw-key
 * renders are intentional; copying backend strings into this owner is not.
 */
export function ConnectMountAction({
  mutationDocument,
  backendClass,
  i18nPrefix,
  invalidateModel,
}: ConnectMountActionProps): React.ReactElement {
  const t = useStorageIntegrateT();
  const [open, setOpen] = React.useState(false);
  const [connect] = useAuthoredMutation(mutationDocument, {
    invalidateModels: [invalidateModel],
  });
  const sourceControl = React.useCallback(
    (props: MutationDialogControlProps) => (
      <MountSourceBrowser {...props} backendClass={backendClass} />
    ),
    [backendClass],
  );
  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "name",
        label: t("mount.connect.name"),
        placeholder: t(`${i18nPrefix}.namePlaceholder`),
        required: true,
      },
      {
        name: "path",
        label: t("mount.connect.sourceFolder"),
        required: true,
        controlLabelMode: "group",
        control: sourceControl,
      },
      {
        name: "mode",
        label: t("mount.connect.mode"),
        widget: "select",
        options: [
          { value: "COPY", label: t("mount.connect.modeCopy") },
          { value: "REFERENCE", label: t("mount.connect.modeReference") },
        ],
        required: true,
      },
    ],
    [i18nPrefix, sourceControl, t],
  );
  const errorFallback = optionalTranslation(t, `${i18nPrefix}.error`);

  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t(`${i18nPrefix}.button`)}
      </Button>
      <MutationDialog
        open={open}
        onOpenChange={setOpen}
        title={t(`${i18nPrefix}.title`)}
        description={t(`${i18nPrefix}.description`)}
        fields={fields}
        initialValues={{ mode: "REFERENCE" }}
        submitLabel={t("mount.connect.submit")}
        submittingLabel={t("mount.connect.submitting")}
        errorFallback={errorFallback}
        parseValues={parseMountConnectValues}
        onSubmit={connect}
        size="lg"
      />
    </>
  );
}

function optionalTranslation(
  t: ReturnType<typeof useStorageIntegrateT>,
  key: string,
): string | undefined {
  const translated = t(key);
  return translated === key ? undefined : translated;
}

function parseMountConnectValues(
  values: MutationDialogValues,
): MountConnectVariables {
  return {
    name: mutationDialogValueCodecs.requiredString(values.name, "name"),
    path: mutationDialogValueCodecs.requiredString(values.path, "path"),
    mode: mountModeValue(values.mode),
  };
}

function mountModeValue(value: unknown): MountConnectMode {
  if (value === "COPY" || value === "REFERENCE") return value;
  throw new TypeError("A mount mode is required.");
}
