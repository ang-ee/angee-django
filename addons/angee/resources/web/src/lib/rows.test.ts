import { describe, expect, test } from "vitest";

import { resourceRows } from "./rows";

describe("resource ledger row projector", () => {
  test("shortens the hash and formats the load timestamp", () => {
    const [row] = resourceRows([
      {
        id: "1",
        sourceAddon: "angee.storage",
        sourcePath: "drives.yaml",
        tier: "install",
        contentHash: "0123456789abcdef0123",
        targetModel: "storage.drive",
        targetId: "drv_1",
        loadedAt: "2026-06-17T09:30:00+00:00",
      },
    ]);
    expect(row?.id).toBe("1");
    expect(row?.source).toBe("angee.storage");
    expect(row?.hash).toBe("0123456789ab");
    expect(row?.loaded).toBe("2026-06-17 09:30:00");
  });
});
