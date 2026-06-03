// Allow side-effect stylesheet imports (e.g. the xy-flow graph stylesheet) to
// type-check without pulling in a bundler's client types.
declare module "*.css";
