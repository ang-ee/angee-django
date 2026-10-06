import { describe, expect, test } from "vitest";

import {
  DND_MIME,
  dragHasAcceptedType,
  dragHasFiles,
  dragSourceProps,
  readDndPayload,
  writeDndPayload,
} from "./dnd";
import type { DragEvent } from "react";
import { testDndTransfer } from "./dnd-test-fixtures";

describe("dnd seam", () => {
  test("round-trips a payload and exposes its type marker during drag", () => {
    const dt = testDndTransfer();
    writeDndPayload(dt, { type: "storage.file", data: { id: "f1" } });

    expect(readDndPayload<{ id: string }>(dt)?.data.id).toBe("f1");
    expect(dt.types).toContain(DND_MIME);
    // Type marker visible without reading the (hidden-on-dragover) body.
    expect(dragHasAcceptedType(dt, "storage.file")).toBe(true);
    expect(dragHasAcceptedType(dt, "storage.folder")).toBe(false);
    expect(dragHasAcceptedType(dt)).toBe(true); // any angee payload
  });

  test("readDndPayload returns null for a foreign/empty transfer", () => {
    expect(readDndPayload(testDndTransfer())).toBeNull();
  });

  test("dragHasFiles recognizes native file drags", () => {
    const empty = testDndTransfer();
    expect(dragHasFiles(empty)).toBe(false);

    const filesType = {
      types: ["Files"],
      files: [] as unknown as FileList,
    } as Pick<DataTransfer, "types" | "files">;
    expect(dragHasFiles(filesType)).toBe(true);
  });

  test("dragSourceProps writes the payload on drag, or stays inert when null", () => {
    expect(dragSourceProps(null)).toBeUndefined();

    const props = dragSourceProps({ type: "storage.file", data: { id: "f1" } });
    expect(props?.draggable).toBe(true);
    const dt = testDndTransfer();
    props?.onDragStart({ dataTransfer: dt } as unknown as DragEvent);
    expect(readDndPayload<{ id: string }>(dt)?.data.id).toBe("f1");
  });
});
