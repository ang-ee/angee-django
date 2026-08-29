import { MAX_PAGE_SIZE } from "@angee/refine";

export const PAGE_SIZE_OPTIONS = [10, 20, 50, 80, MAX_PAGE_SIZE] as const;
export const DEFAULT_PAGE_SIZE = PAGE_SIZE_OPTIONS[2];
