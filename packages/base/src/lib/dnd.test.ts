import { describe, expect, test } from "vitest";

import {
  DND_MIME,
  dragHasAcceptedType,
  readDndPayload,
  writeDndPayload,
} from "./dnd";

/** A minimal DataTransfer stand-in (happy-dom's is incomplete for setData). */
function fakeTransfer(): DataTransfer {
  const store = new Map<string, string>();
  return {
    setData: (type: string, data: string) => store.set(type.toLowerCase(), data),
    getData: (type: string) => store.get(type.toLowerCase()) ?? "",
    get types() {
      return [...store.keys()];
    },
    effectAllowed: "none",
    dropEffect: "none",
  } as unknown as DataTransfer;
}

describe("dnd seam", () => {
  test("round-trips a payload and exposes its type marker during drag", () => {
    const dt = fakeTransfer();
    writeDndPayload(dt, { type: "storage.file", data: { id: "f1" } });

    expect(readDndPayload<{ id: string }>(dt)?.data.id).toBe("f1");
    expect(dt.types).toContain(DND_MIME);
    // Type marker visible without reading the (hidden-on-dragover) body.
    expect(dragHasAcceptedType(dt, "storage.file")).toBe(true);
    expect(dragHasAcceptedType(dt, "storage.folder")).toBe(false);
    expect(dragHasAcceptedType(dt)).toBe(true); // any angee payload
  });

  test("readDndPayload returns null for a foreign/empty transfer", () => {
    expect(readDndPayload(fakeTransfer())).toBeNull();
  });
});
