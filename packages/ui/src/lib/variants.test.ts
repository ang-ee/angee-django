import { describe, expect, test } from "vitest";
import { recipeAxes, tv } from "./variants";

describe("recipeAxes", () => {
  test("reads extended native variants, sorts axes and coerces boolean keys", () => {
    const base = tv({ variants: { disabled: { true: "opacity-50", false: "" } } });
    const recipe = tv({ extend: base, variants: { size: { sm: "p-1", lg: "p-4" }, state: { idle: "", busy: "animate-pulse" } } });
    const axes = recipeAxes(recipe);
    expect(Object.keys(axes)).toEqual(["disabled", "size", "state"]);
    expect(axes).toEqual({ disabled: [false, true], size: ["lg", "sm"], state: ["busy", "idle"] });
    const disabled: readonly boolean[] = axes.disabled;
    const size: readonly ("lg" | "sm")[] = axes.size;
    expect(disabled.length + size.length).toBe(4);
  });

  test("recipes without variants have no axes", () => {
    expect(recipeAxes(tv({ base: "flex" }))).toEqual({});
  });
});
