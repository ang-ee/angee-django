import { useAuthoredMutation } from "@angee/refine";
import {
  Button,
  DialogForm,
  ErrorBanner,
  FieldLabel,
  FieldRoot,
  Glyph,
  Input,
  Select,
  errorMessage,
} from "@angee/ui";
import * as React from "react";

import { ConnectLocalFolder, MOUNT_MODEL } from "./documents";
import { useStorageIntegrateT } from "./i18n";
import { MountSourceBrowser } from "./MountSourceBrowser";

type MountMode = "COPY" | "REFERENCE";

/** Toolbar action for provisioning a local-folder Mount. */
export function ConnectLocalFolderAction(): React.ReactElement {
  const t = useStorageIntegrateT();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [path, setPath] = React.useState("");
  const [mode, setMode] = React.useState<MountMode>("REFERENCE");
  const [error, setError] = React.useState<string | null>(null);
  const [connect, connectState] = useAuthoredMutation(ConnectLocalFolder, {
    invalidateModels: [MOUNT_MODEL],
  });

  React.useEffect(() => {
    if (!open) {
      setName("");
      setPath("");
      setMode("REFERENCE");
      setError(null);
    }
  }, [open]);

  const modeOptions = React.useMemo(
    () => [
      { value: "COPY", label: t("mount.connect.modeCopy") },
      { value: "REFERENCE", label: t("mount.connect.modeReference") },
    ],
    [t],
  );
  const ready = name.trim() !== "" && path !== "";
  const footer = (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={connectState.fetching}
        onClick={() => setOpen(false)}
      >
        {t("mount.connect.cancel")}
      </Button>
      <Button
        type="submit"
        variant="primary"
        size="sm"
        disabled={!ready || connectState.fetching}
        loading={connectState.fetching}
        loadingText={t("mount.connect.submitting")}
      >
        {t("mount.connect.submit")}
      </Button>
    </>
  );

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!ready || connectState.fetching) return;
    setError(null);
    try {
      await connect({ name: name.trim(), path, mode });
      setOpen(false);
    } catch (cause) {
      setError(errorMessage(cause, t("mount.connect.error")));
    }
  }

  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t("mount.connect.button")}
      </Button>
      <DialogForm
        open={open}
        onOpenChange={setOpen}
        title={t("mount.connect.title")}
        description={t("mount.connect.description")}
        size="lg"
        footer={footer}
        onSubmit={(event) => void submit(event)}
      >
        <FieldRoot>
          <FieldLabel htmlFor="mount-local-folder-name" required>
            {t("mount.connect.name")}
          </FieldLabel>
          <Input
            id="mount-local-folder-name"
            aria-label={t("mount.connect.name")}
            placeholder={t("mount.connect.namePlaceholder")}
            value={name}
            required
            disabled={connectState.fetching}
            onChange={(event) => setName(event.currentTarget.value)}
          />
        </FieldRoot>

        <MountSourceBrowser
          backendClass="local_folder"
          open={open}
          value={path}
          onChange={setPath}
        />

        <FieldRoot>
          <FieldLabel htmlFor="mount-local-folder-mode" required>
            {t("mount.connect.mode")}
          </FieldLabel>
          <Select
            id="mount-local-folder-mode"
            aria-label={t("mount.connect.mode")}
            options={modeOptions}
            value={mode}
            disabled={connectState.fetching}
            onValueChange={(value) => setMode(mountModeValue(value))}
          />
        </FieldRoot>
        <ErrorBanner description={error} />
      </DialogForm>
    </>
  );
}

function mountModeValue(value: string): MountMode {
  if (value === "COPY" || value === "REFERENCE") return value;
  throw new Error("A mount mode is required.");
}
