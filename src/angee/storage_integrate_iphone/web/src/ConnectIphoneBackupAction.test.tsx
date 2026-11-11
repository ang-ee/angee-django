// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  props: null as Record<string, unknown> | null,
}));

vi.mock("./documents", () => ({
  ConnectIphoneBackup: "ConnectIphoneBackup",
}));

vi.mock("@angee/storage-integrate", () => ({
  ConnectMountAction: (props: Record<string, unknown>) => {
    actionMocks.props = props;
    return <button type="button">connect iPhone backup</button>;
  },
  MOUNT_MODEL: "storage_integrate.Mount",
}));

import { ConnectIphoneBackupAction } from "./ConnectIphoneBackupAction";

describe("ConnectIphoneBackupAction", () => {
  beforeEach(() => {
    actionMocks.props = null;
  });

  test("declares the iPhone backend through the shared Mount action", () => {
    render(<ConnectIphoneBackupAction />);

    expect(actionMocks.props).toMatchObject({
      mutationDocument: "ConnectIphoneBackup",
      backendClass: "iphone_backup",
      i18nPrefix: "iphone.mount.connect",
      invalidateModel: "storage_integrate.Mount",
    });
  });
});
