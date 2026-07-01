// Allow side-effect stylesheet imports owned by rendered app-shell pages
// (the login page pulls in `@angee/logo-react/style.css`). Mirrors the same
// ambient `@angee/ui` declares for its rendered view bindings.
declare module "*.css";
