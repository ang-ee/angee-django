import type { RuntimeBrand } from "@angee/ui/runtime";
import type { ShellSettings } from "./define-addon";

export function assertBrand(where: string, brand: unknown): asserts brand is RuntimeBrand;
export function assertShellSettings(shell: unknown): asserts shell is ShellSettings;
