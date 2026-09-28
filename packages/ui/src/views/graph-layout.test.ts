import { describe, expect, test } from "vitest";
import { findFreeGraphPosition, layoutGraph, placeGraphNodeBeside } from "./graph-layout";

describe("graph layout", () => {
  test("is deterministic, preserves explicit positions, and tolerates cycles and missing endpoints", () => {
    const input = {
      nodes: [{ id: "a", width: 100, height: 60, position: { x: 4, y: 8 } }, { id: "b", width: 100, height: 60 }],
      edges: [{ id: "ab", source: "a", target: "b", kind: "line" }, { id: "ba", source: "b", target: "a", kind: "line" }, { id: "aa", source: "a", target: "a", kind: "line" }, { id: "ac", source: "a", target: "c", kind: "line" }],
    };
    const result = layoutGraph(input);
    expect(result).toEqual(layoutGraph(input));
    expect(result.positions.get("a")).toEqual({ x: 4, y: 8 });
    expect([...result.visibleEdgeIds]).toEqual(["ab", "ba", "aa"]);
    expect(Number.isFinite(result.positions.get("b")?.x)).toBe(true);
  });

  test("places a new node in a free lane beside its anchor", () => {
    const anchor = { x: 10, y: 20, width: 120, height: 60 };
    const size = { width: 120, height: 60 };
    expect(placeGraphNodeBeside(anchor, size)).toEqual({ x: 10, y: 156 });
    expect(placeGraphNodeBeside(anchor, size, [], "right")).toEqual({ x: 164, y: 20 });
    expect(placeGraphNodeBeside(anchor, size, [{ ...size, x: 10, y: 156 }])).toEqual({ x: 164, y: 156 });
  });

  test("escapes a wide obstruction even when its nearest lanes overlap", () => {
    expect(findFreeGraphPosition({ width: 100, height: 60 }, { x: 500, y: 0 }, [{ x: 0, y: 0, width: 1000, height: 60 }])).toEqual({ x: 1034, y: 0 });
    expect(layoutGraph({ nodes: [], edges: [] }).positions.size).toBe(0);
  });
});
