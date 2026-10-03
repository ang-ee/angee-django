/** Whether the bundle runs in development: Vite's `DEV` flag, else `NODE_ENV` is not production. */
export function developmentMode(): boolean {
  const viteEnv = (
    import.meta as ImportMeta & { readonly env?: { readonly DEV?: boolean } }
  ).env;
  if (typeof viteEnv?.DEV === "boolean") return viteEnv.DEV;
  const nodeEnv = (
    globalThis as typeof globalThis & {
      process?: { env?: { NODE_ENV?: string } };
    }
  ).process?.env?.NODE_ENV;
  return nodeEnv !== "production";
}
