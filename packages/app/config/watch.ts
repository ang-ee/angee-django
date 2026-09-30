// Paths no Angee dev server or test watcher may watch. Jujutsu keeps its store
// and working-copy lock under `.jj/`; a watcher on it slows Vitest startup, times
// out `jj` commands, and corrupts `working_copy.lock` (see the jj FAQ). Vite
// already ignores `.git/` and `node_modules/` on its own. Shared by the dev-server
// builder (`./vite`) and the Vitest builders (`./vitest`) so neither module has
// to import the other's dependencies.
export const ANGEE_WATCH_IGNORED: readonly string[] = ["**/.jj/**"];
