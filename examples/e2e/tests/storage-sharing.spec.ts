import { createHash } from "node:crypto";

import type { APIRequestContext } from "@playwright/test";
import {
  expect,
  grantRecordAccess,
  GraphQLClient,
  PUBLIC_GRAPHQL_PATH,
  requireGraphQLData,
  revokeRecordAccess,
  roleStatePath,
  test,
  type RecordAccessGrant,
} from "@angee/e2e";

interface Principal {
  username: string;
  assignment_subject: string;
}

interface CreatedRecord {
  id: string;
}

interface StorageRows {
  drives: Array<{ id: string; name: string }>;
  folders: Array<{ id: string; name: string; drive: string | null }>;
  files: Array<{ id: string; filename: string; drive: string; folder: string | null }>;
}

interface UploadBegin {
  method: string;
  upload_url: string;
  error: string | null;
  file: CreatedRecord | null;
}

const INVENTORY = `
  query StorageSharingInventory {
    users(limit: 100) { username assignment_subject }
    backends(limit: 1) { id }
  }
`;

const CREATE_DRIVE = `
  mutation CreateSharingDrive($object: drives_insert_input!) {
    insert_drives_one(object: $object) { id }
  }
`;

const DELETE_DRIVE = `
  mutation DeleteSharingDrive($id: String!) {
    delete_drives_by_pk(id: $id) { id }
  }
`;

const CREATE_FOLDER = `
  mutation CreateSharingFolder($object: folders_insert_input!) {
    insert_folders_one(object: $object) { id }
  }
`;

const DELETE_FOLDER = `
  mutation DeleteSharingFolder($id: ID!) {
    delete_folder(id: $id, confirm: true) { total_deleted_count has_blockers }
  }
`;

const BEGIN_UPLOAD = `
  mutation BeginSharingUpload($input: FileUploadBeginInput!) {
    file_upload_begin(input: $input) {
      method
      upload_url
      error
      file { id }
    }
  }
`;

const FINALIZE_UPLOAD = `
  mutation FinalizeSharingUpload($input: FileUploadFinalizeInput!) {
    file_upload_finalize(input: $input) { error file { id } }
  }
`;

const PURGE_FILE = `
  mutation PurgeSharingFile($id: ID!) {
    purge_file(id: $id)
  }
`;

const STORAGE_ROWS = `
  query SharedStorageRows(
    $driveId: String!
    $folderId: String!
    $fileIds: [String!]!
  ) {
    drives(where: { id: { _eq: $driveId } }) { id name }
    folders(where: { id: { _eq: $folderId } }) { id name drive }
    files(where: { id: { _in: $fileIds } }) { id filename drive folder }
  }
`;

test("Drive and File grants share storage between users", async ({ browser }) => {
  test.setTimeout(90_000);
  const contexts = {
    admin: await browser.newContext({ storageState: roleStatePath("admin") }),
    alice: await browser.newContext({ storageState: roleStatePath("alice") }),
    bob: await browser.newContext({ storageState: roleStatePath("bob") }),
  };
  const apis = {
    alice: new GraphQLClient(contexts.alice.request, PUBLIC_GRAPHQL_PATH),
    bob: new GraphQLClient(contexts.bob.request, PUBLIC_GRAPHQL_PATH),
  };
  const aliceConsole = new GraphQLClient(
    contexts.alice.request,
    "/graphql/console/",
  );
  const adminConsole = new GraphQLClient(
    contexts.admin.request,
    "/graphql/console/",
  );
  let driveId: string | undefined;
  let folderId: string | undefined;
  const fileIds: string[] = [];
  const grants: Array<{
    client: GraphQLClient;
    grant: RecordAccessGrant;
  }> = [];

  const grant = async (
    client: GraphQLClient,
    targetType: string,
    targetId: string,
    relation: string,
    subject: string,
  ): Promise<void> => {
    const access = await grantRecordAccess(client, {
      targetType,
      targetId,
      relation,
      subject,
    });
    grants.push({ client, grant: access });
  };

  const upload = async (
    client: GraphQLClient,
    request: APIRequestContext,
    filename: string,
    body: string,
    folder: string | null,
  ): Promise<string> => {
    const contentHash = createHash("sha256").update(body).digest("hex");
    const sizeBytes = Buffer.byteLength(body);
    const begun = requireGraphQLData<{ file_upload_begin: UploadBegin }>(
      await client.query(BEGIN_UPLOAD, {
        input: {
          filename,
          mime_type: "text/plain",
          size_bytes: sizeBytes,
          drive: driveId,
          folder,
          content_hash: contentHash,
        },
      }),
    ).file_upload_begin;
    expect(begun.error).toBeNull();
    expect(begun.file).not.toBeNull();
    const fileId = begun.file!.id;
    fileIds.push(fileId);
    expect(begun.method).toBe("proxy");
    const transferred = await request.put(begun.upload_url, {
      data: Buffer.from(body),
      headers: { "content-type": "text/plain" },
    });
    expect(transferred.ok()).toBe(true);
    const finalized = requireGraphQLData<{
      file_upload_finalize: { error: string | null; file: CreatedRecord | null };
    }>(await client.query(FINALIZE_UPLOAD, {
      input: {
        file: fileId,
        content_hash: contentHash,
        size_bytes: sizeBytes,
      },
    })).file_upload_finalize;
    expect(finalized.error).toBeNull();
    expect(finalized.file?.id).toBe(fileId);
    return fileId;
  };

  try {
    const inventory = requireGraphQLData<{
      users: Principal[];
      backends: CreatedRecord[];
    }>(await adminConsole.query(INVENTORY));
    const principal = (username: string): Principal => {
      const row = inventory.users.find((user) => user.username === username);
      expect(row, `Missing demo user ${username}`).toBeDefined();
      return row!;
    };
    const alice = principal("alice");
    const bob = principal("bob");
    const backend = inventory.backends[0];
    expect(backend).toBeDefined();

    const suffix = `${Date.now()}`;
    const driveName = `E2E private sharing ${suffix}`;
    const folderName = `Alice folder ${suffix}`;
    const directName = `alice-direct-${suffix}.txt`;
    const inheritedName = `alice-folder-${suffix}.txt`;
    driveId = requireGraphQLData<{ insert_drives_one: CreatedRecord }>(
      await adminConsole.query(CREATE_DRIVE, {
        object: {
          backend: backend!.id,
          slug: `e2e-private-sharing-${suffix}`,
          name: driveName,
          prefix: `e2e-private-sharing-${suffix}`,
        },
      }),
    ).insert_drives_one.id;

    await grant(
      adminConsole,
      "storage/drive",
      driveId,
      "editor",
      alice.assignment_subject,
    );
    folderId = requireGraphQLData<{ insert_folders_one: CreatedRecord }>(
      await apis.alice.query(CREATE_FOLDER, {
        object: { drive: driveId, name: folderName, parent: null },
      }),
    ).insert_folders_one.id;
    const inheritedFileId = await upload(
      apis.alice,
      contexts.alice.request,
      inheritedName,
      `Folder inherited access ${suffix}`,
      folderId,
    );
    const directFileId = await upload(
      apis.alice,
      contexts.alice.request,
      directName,
      `Direct file access ${suffix}`,
      null,
    );

    const visible = async (): Promise<StorageRows> =>
      requireGraphQLData(
        await apis.bob.query<StorageRows>(STORAGE_ROWS, {
          driveId,
          folderId,
          fileIds,
        }),
      );
    expect(await visible()).toEqual({ drives: [], folders: [], files: [] });

    await grant(
      aliceConsole,
      "storage/file",
      directFileId,
      "viewer",
      bob.assignment_subject,
    );
    let rows = await visible();
    expect(rows.drives).toEqual([]);
    expect(rows.folders).toEqual([]);
    expect(rows.files.map((file) => file.id)).toEqual([directFileId]);

    const bobPage = await contexts.bob.newPage();
    await bobPage.goto(`/storage/${directFileId}`);
    await expect(
      bobPage.getByRole("heading", { name: directName, exact: true }),
    ).toBeVisible({ timeout: 20_000 });

    await grant(
      aliceConsole,
      "storage/drive",
      driveId,
      "viewer",
      bob.assignment_subject,
    );
    rows = await visible();
    expect(rows.drives.map((drive) => drive.id)).toEqual([driveId]);
    expect(rows.folders.map((folder) => folder.id)).toEqual([folderId]);
    expect(new Set(rows.files.map((file) => file.id))).toEqual(
      new Set([inheritedFileId, directFileId]),
    );

    await bobPage.goto("/storage");
    const openPrimaryPanel = bobPage.getByRole("button", {
      name: "Open primary panel",
    });
    const drivePicker = bobPage.getByRole("combobox", { name: /^Drive:/ });
    await expect(openPrimaryPanel.or(drivePicker)).toBeVisible({ timeout: 20_000 });
    if (await openPrimaryPanel.count()) await openPrimaryPanel.click();
    await expect(drivePicker).toBeVisible({ timeout: 20_000 });
    await drivePicker.click();
    await bobPage.getByRole("option", { name: driveName, exact: true }).click();
    const folder = bobPage.getByRole("treeitem").filter({ hasText: folderName });
    await expect(folder).toBeVisible({ timeout: 20_000 });
    await folder.click();
    const removeGroup = bobPage.getByRole("button", { name: "Remove Folder" });
    await expect(removeGroup).toBeVisible({ timeout: 20_000 });
    await removeGroup.click();
    await expect(
      bobPage.getByRole("row").filter({ hasText: inheritedName }),
    ).toBeVisible({ timeout: 20_000 });
  } finally {
    for (const access of grants.reverse()) {
      await revokeRecordAccess(access.client, access.grant);
    }
    for (const id of fileIds.reverse()) {
      await adminConsole.query(PURGE_FILE, { id });
    }
    if (folderId !== undefined) {
      await adminConsole.query(DELETE_FOLDER, { id: folderId });
    }
    if (driveId !== undefined) {
      await adminConsole.query(DELETE_DRIVE, { id: driveId });
    }
    await Promise.all(Object.values(contexts).map((context) => context.close()));
  }
});
