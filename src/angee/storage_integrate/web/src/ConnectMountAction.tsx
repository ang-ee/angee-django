import { CONNECT_WITH_ACCOUNT_FIELD, useConnectWithAccountField, useConnectWithAccountQuery } from "@angee/integrate";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
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

import { ConnectMount, MOUNT_MODEL, MountBackendFacts, MountCopyTargets } from "./documents";
import { useStorageIntegrateT } from "./i18n";
import { MountSourceBrowser } from "./MountSourceBrowser";

export type MountConnectMode = "COPY" | "REFERENCE";

export type MountConnectVariables = {
  backendClass: string;
  name: string;
  root: string;
  mode: MountConnectMode;
  credentialId?: string;
  copyBackendId?: string;
};

export interface ConnectMountActionProps {
  backendClass: string;
  i18nPrefix: string;
}

/**
 * Canonical economy for browsable Mount connection: this factory owns the
 * button, typed dialog, browser field, copy target, generic labels, busy copy,
 * the one `connect_mount` mutation, and invalidation; the account field is
 * integrate's connect-with-account field. What a backend asks for (an account,
 * which accounts) comes from the server's `mount_backend`; backends supply only
 * their class and genuinely varying button/title/description/placeholder/error range.
 *
 * Backend message ranges arrive through addon composition. Providerless raw-key
 * renders are intentional; copying backend strings into this owner is not.
 */
export function ConnectMountAction({
  backendClass,
  i18nPrefix,
}: ConnectMountActionProps): React.ReactElement {
  const t = useStorageIntegrateT();
  const [connect] = useAuthoredMutation(ConnectMount, {
    invalidateModels: [MOUNT_MODEL],
  });
  const factsQuery = useConnectWithAccountQuery(MountBackendFacts, { backendClass });
  const facts = factsQuery.data?.mount_backend;
  const targets = useAuthoredQuery(MountCopyTargets).data?.backends;
  const requiresCredential = facts?.requires_credential === true;
  const accountField = useConnectWithAccountField({
    facts,
    returnRoute: "storage-integrate.mounts",
    onConnected: factsQuery.onConnected,
  });
  const sourceControl = React.useCallback(
    (props: DescriptorFieldControlProps) => {
      const credentialId =
        mutationDialogValueCodecs.string(props.dialogValues[CONNECT_WITH_ACCOUNT_FIELD]) ?? undefined;
      // A new account browses afresh: its folders are another account's.
      return (
        <MountSourceBrowser {...props} key={credentialId ?? ""} backendClass={backendClass}
          credentialId={credentialId} requiresCredential={requiresCredential} />
      );
    },
    [backendClass, requiresCredential],
  );
  const copyTargetOptions = React.useMemo(
    () => (targets ?? [])
      .filter((target) => target.writable && !target.is_archived)
      .map((target) => ({ value: target.id, label: target.label })),
    [targets],
  );
  const fields = React.useMemo<readonly DescriptorField[]>(
    () => [
      {
        name: "name",
        label: t("mount.connect.name"),
        placeholder: t(`${i18nPrefix}.namePlaceholder`),
        required: true,
      },
      ...(requiresCredential
        ? [{
          ...accountField,
          // The chosen folder belongs to the account it was browsed with.
          branchReset: () => ({ fields: ["root"], values: {} }),
        }]
        : []),
      {
        name: "root",
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
      {
        name: "copyBackendId",
        label: t("mount.connect.copyBackend"),
        placeholder: t("mount.connect.copyBackendDefault"),
        widget: "select",
        options: copyTargetOptions,
        showWhen: (values: Record<string, unknown>) => values.mode === "COPY",
      },
    ],
    [accountField, copyTargetOptions, i18nPrefix, requiresCredential, sourceControl, t],
  );
  const errorFallback = optionalTranslation(t, `${i18nPrefix}.error`);
  const parseValues = React.useCallback(
    (values: MutationDialogValues) => parseMountConnectValues(backendClass, values),
    [backendClass],
  );

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
        parseValues={parseValues}
        onSubmit={async (values) => ({ status: "ok", data: await connect(values) })}
        size="lg"
    />
  );
}

function parseMountConnectValues(
  backendClass: string,
  values: MutationDialogValues,
): MountConnectVariables {
  const variables: MountConnectVariables = {
    backendClass,
    name: mutationDialogValueCodecs.requiredString(values.name, "name"),
    root: mutationDialogValueCodecs.requiredString(values.root, "root"),
    mode: mountModeValue(values.mode),
  };
  const credentialId = mutationDialogValueCodecs.string(values[CONNECT_WITH_ACCOUNT_FIELD]);
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
