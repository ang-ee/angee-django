import {
  useAuthoredMutation,
  type TypedDocumentNode,
} from "@angee/refine";
import {
  ActionTrigger,
  MutationDialog,
  mutationDialogValueCodecs,
  optionalTranslation,
  type DescriptorFieldControlProps,
  type DescriptorField,
  type MutationDialogValues,
} from "@angee/ui";
import * as React from "react";

import { useStorageIntegrateT } from "./i18n";
import { MountAccountPicker } from "./MountAccountPicker";
import { MountSourceBrowser } from "./MountSourceBrowser";

export type MountConnectMode = "COPY" | "REFERENCE";

export type MountConnectVariables = {
  name: string;
  path: string;
  mode: MountConnectMode;
  credentialId?: string;
  copyBackendId?: string;
};

const BACKEND_MODEL = "storage.Backend";

export interface ConnectMountActionProps {
  mutationDocument: TypedDocumentNode<unknown, MountConnectVariables>;
  backendClass: string;
  i18nPrefix: string;
  invalidateModel: string;
  /** The backend reads its source with one of the user's accounts (`MountBackend.requires_credential`). */
  requiresAccount?: boolean;
  /** The mutation takes `copyBackendId`, so a copy mount may store in a chosen writable backend. */
  choosesCopyBackend?: boolean;
}

/**
 * Canonical economy for browsable Mount connection: this factory owns the
 * button, typed dialog, account and browser fields, copy target, generic labels,
 * busy copy, mutation, and invalidation. Backends supply only their
 * operation/class, whether they read with an account, and genuinely varying
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
  requiresAccount = false,
  choosesCopyBackend = false,
}: ConnectMountActionProps): React.ReactElement {
  const t = useStorageIntegrateT();
  const [connect] = useAuthoredMutation(mutationDocument, {
    invalidateModels: [invalidateModel],
  });
  const accountControl = React.useCallback(
    (props: DescriptorFieldControlProps) => (
      <MountAccountPicker {...props} backendClass={backendClass} />
    ),
    [backendClass],
  );
  const sourceControl = React.useCallback(
    (props: DescriptorFieldControlProps) => {
      const credentialId = mutationDialogValueCodecs.string(props.dialogValues.credentialId) ?? undefined;
      // A new account browses afresh: its folders are another account's.
      return (
        <MountSourceBrowser {...props} key={credentialId ?? ""} backendClass={backendClass}
          credentialId={credentialId} requiresAccount={requiresAccount} />
      );
    },
    [backendClass, requiresAccount],
  );
  const fields = React.useMemo<readonly DescriptorField[]>(
    () => [
      {
        name: "name",
        label: t("mount.connect.name"),
        placeholder: t(`${i18nPrefix}.namePlaceholder`),
        required: true,
      },
      ...(requiresAccount
        ? [{
          name: "credentialId",
          label: t("mount.connect.account"),
          required: true,
          controlLabelMode: "group" as const,
          control: accountControl,
          // The chosen folder belongs to the account it was browsed with.
          branchReset: () => ({ fields: ["path"], values: {} }),
        }]
        : []),
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
      ...(choosesCopyBackend
        ? [{
          name: "copyBackendId",
          label: t("mount.connect.copyBackend"),
          placeholder: t("mount.connect.copyBackendDefault"),
          relation: {
            resource: BACKEND_MODEL,
            labelField: "label",
            filters: [{ field: "is_archived", operator: "eq" as const, value: false }],
          },
          showWhen: (values: Record<string, unknown>) => values.mode === "COPY",
        }]
        : []),
    ],
    [accountControl, choosesCopyBackend, i18nPrefix, requiresAccount, sourceControl, t],
  );
  const errorFallback = optionalTranslation(t, `${i18nPrefix}.error`);

  return (
    <MutationDialog
      trigger={<ActionTrigger glyph="plus">{t(`${i18nPrefix}.button`)}</ActionTrigger>}
        title={t(`${i18nPrefix}.title`)}
        description={t(`${i18nPrefix}.description`)}
        fields={fields}
        initialValues={{ mode: "REFERENCE" }}
        submitLabel={t("mount.connect.submit")}
        submittingLabel={t("mount.connect.submitting")}
        errorFallback={errorFallback}
        parseValues={parseMountConnectValues}
        onSubmit={async (values) => ({ status: "ok", data: await connect(values) })}
        size="lg"
    />
  );
}

function parseMountConnectValues(
  values: MutationDialogValues,
): MountConnectVariables {
  const variables: MountConnectVariables = {
    name: mutationDialogValueCodecs.requiredString(values.name, "name"),
    path: mutationDialogValueCodecs.requiredString(values.path, "path"),
    mode: mountModeValue(values.mode),
  };
  const credentialId = mutationDialogValueCodecs.string(values.credentialId);
  if (credentialId) variables.credentialId = credentialId;
  const copyBackendId = mutationDialogValueCodecs.string(values.copyBackendId);
  if (copyBackendId && variables.mode === "COPY") variables.copyBackendId = copyBackendId;
  return variables;
}

function mountModeValue(value: unknown): MountConnectMode {
  if (value === "COPY" || value === "REFERENCE") return value;
  // The dialog's required select never submits another value; this guards the wire type.
  throw new TypeError("mode");
}
